from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "cdu_picking")
class TestPrescriptionLevelPicking(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.facility = cls.env["cdu.facility"].create(
            {"name": "Picking Test Facility", "code": "PICK-FAC"}
        )
        cls.collection_point = cls.env["cdu.collection.point"].create(
            {
                "name": "Picking Test Collection Point",
                "code": "PICK-CP",
                "point_type": "e_locker",
            }
        )
        cls.batch = cls.env["cdu.batch"].create({"state": "confirmed"})
        cls.pickup_date = fields.Date.today()
        cls.prescriptions = cls.env["cdu.prescription"]
        cls.patient_lines = cls.env["cdu.batch.patient.line"]
        for index in range(1, 4):
            patient = cls.env["res.partner"].create(
                {
                    "name": "Picking Patient %s" % index,
                    "customer_rank": 1,
                    "cdu_eregister_id": "PICK-PAT-%s" % index,
                }
            )
            prescription = cls.env["cdu.prescription"].create(
                {
                    "facility_id": cls.facility.id,
                    "facility_name": cls.facility.name,
                    "facility_code": cls.facility.code,
                    "prescription_date": cls.pickup_date,
                    "patient_id": patient.id,
                    "patient_identifier": patient.cdu_eregister_id,
                    "patient_first_name": patient.name,
                    "regimen_prescribed_raw": "REGIMEN-A",
                    "dosage_instructions": "Take ten units daily.",
                    "drug_pickup_point_raw": cls.collection_point.name,
                    "collection_point_id": cls.collection_point.id,
                    "next_drug_pickup_date": cls.pickup_date,
                    "batch_id": cls.batch.id,
                    "state": "awaiting_picking",
                }
            )
            patient_line = cls.env["cdu.batch.patient.line"].create(
                {
                    "batch_id": cls.batch.id,
                    "prescription_id": prescription.id,
                    "patient_id": patient.id,
                    "drug_name": "REGIMEN-A",
                    "cdu_days": 30,
                    "repeat_days": 30,
                    "effective_repeat_days": 30,
                    "daily_dose": 10,
                    "required_quantity": 300,
                    "required_units": 300,
                }
            )
            cls.prescriptions |= prescription
            cls.patient_lines |= patient_line

        cls.summary = cls.env["cdu.batch.picking.line"].create(
            {
                "batch_id": cls.batch.id,
                "unmapped_drug_name": "REGIMEN-A",
                "total_tablets": 900,
                "prescription_count": 3,
            }
        )
        cls.picking_line = cls.env["cdu.picking.line"].create(
            {
                "batch_id": cls.batch.id,
                "summary_line_id": cls.summary.id,
                "openmrs_drug_name": "REGIMEN-A",
                "prescription_count": 3,
                "quantity_to_pick": 30,
                "allocation_mode": "individual",
            }
        )
        cls.picking_line._ensure_patient_resolutions()
        cls.resolutions = cls.picking_line.resolution_ids.sorted("sequence")

        cls.stock_30_lot_1 = cls._create_stock(
            "PRODUCT-A", "Product A 30", 30, "A30-1", 20
        )
        cls.stock_30_lot_2 = cls._create_stock(
            "PRODUCT-A", "Product A 30", 30, "A30-2", 20
        )
        cls.stock_60 = cls._create_stock(
            "PRODUCT-A-60", "Product A 60", 60, "A60-1", 20
        )
        cls.stock_15 = cls._create_stock(
            "PRODUCT-B-15", "Alternative Product 15", 15, "B15-1", 20
        )

    @classmethod
    def _create_stock(
        cls,
        code,
        name,
        pack_size,
        lot,
        stock,
        expiration_date=None,
    ):
        values = {
            "batch_id": cls.batch.id,
            "facility_code": "TEST-CDU",
            "program_code": "TEST-PROGRAM",
            "orderable_code": code,
            "orderable_name": name,
            "pack_size": pack_size,
            "lot": lot,
            "stock_on_hand": stock,
        }
        if expiration_date:
            values["expiration_date"] = expiration_date
        return cls.env["cdu.elmis.stock.option"].create(values)

    def _allocate(self, resolution, option, packs, daily_units=None):
        values = {
            "batch_id": self.batch.id,
            "picking_line_id": self.picking_line.id,
            "resolution_id": resolution.id,
            "stock_option_id": option.id,
            "quantity_packs": packs,
            "allocation_method": self.picking_line.allocation_mode,
        }
        if daily_units is not None:
            values["daily_units"] = daily_units
        return self.env["cdu.picking.patient.allocation"].create(values)

    def test_individual_allocation_supports_mixed_products_pack_sizes_and_lots(self):
        resolution = self.resolutions[0]
        self._allocate(resolution, self.stock_30_lot_1, 1, daily_units=2)
        self._allocate(resolution, self.stock_60, 4, daily_units=4)
        self._allocate(resolution, self.stock_15, 2, daily_units=1)

        self.assertEqual(resolution.allocated_packs, 7)
        self.assertEqual(resolution.supplied_units, 300)
        self.assertTrue(resolution.coverage_confirmation_required)
        self.assertFalse(resolution.coverage_confirmed)
        self.assertEqual(resolution.status, "draft")
        resolution.confirmed_supplied_days = 30
        self.assertTrue(resolution.coverage_confirmed)
        self.assertEqual(resolution.coverage_days, 30)
        self.assertEqual(resolution.status, "full")
        self.assertEqual(
            set(resolution.allocation_line_ids.mapped("selected_pack_size")),
            {15, 30, 60},
        )
        self.assertEqual(
            sum(self.batch.elmis_picking_fulfilment_line_ids.mapped("quantity_picked")),
            7,
        )

    def test_extra_packs_increase_selected_patient_coverage(self):
        resolution = self.resolutions[0]
        self._allocate(resolution, self.stock_60, 6)
        self.resolutions[1:].action_mark_unserved()

        self.batch._apply_repeat_fulfilment_results()

        patient_line = resolution.patient_line_id
        self.assertEqual(patient_line.picked_units, 360)
        self.assertEqual(patient_line.actual_supplied_days, 36)
        self.assertEqual(patient_line.back_order_days, 0)
        self.assertEqual(
            resolution.prescription_id.next_drug_pickup_date,
            self.pickup_date + timedelta(days=36),
        )

    def test_daily_units_are_audited_and_recalculate_without_changing_packs(self):
        resolution = self.resolutions[0]
        allocation = self._allocate(resolution, self.stock_30_lot_1, 10)

        self.assertEqual(allocation.daily_units, 10)
        self.assertEqual(allocation.original_daily_units, 10)
        allocation.daily_units = 20

        self.assertEqual(allocation.quantity_packs, 10)
        self.assertEqual(allocation.suggested_packs, 20)
        self.assertEqual(resolution.supplied_units, 300)
        self.assertEqual(resolution.coverage_days, 15)
        self.assertEqual(resolution.coverage_variance_days, -15)
        self.assertEqual(resolution.status, "partial")
        self.assertEqual(resolution.patient_line_id.daily_dose, 10)
        self.assertEqual(resolution.patient_line_id.actual_supplied_days, 15)
        self.assertEqual(allocation.daily_units_changed_by, self.env.user)
        self.assertTrue(allocation.daily_units_changed_at)
        self.assertEqual(
            resolution.prescription_id.dosage_instructions,
            "Take ten units daily.",
        )

    def test_daily_units_must_be_positive_whole_numbers(self):
        resolution = self.resolutions[0]
        allocation = self._allocate(resolution, self.stock_30_lot_1, 1)
        for invalid_value in (0, -1, 1.5, "not-a-number"):
            with self.assertRaisesRegex(
                ValidationError, "whole number greater than zero"
            ):
                allocation.write({"daily_units": invalid_value})

    def test_same_product_lots_share_daily_units(self):
        resolution = self.resolutions[0]
        first = self._allocate(
            resolution, self.stock_30_lot_1, 2, daily_units=4
        )
        second = self._allocate(resolution, self.stock_30_lot_2, 2)

        self.assertEqual(second.daily_units, 4)
        self.assertEqual(resolution.distinct_product_count, 1)
        second.daily_units = 2
        self.assertEqual(first.daily_units, 2)
        self.assertEqual(resolution.coverage_days, 60)
        with self.assertRaisesRegex(
            ValidationError, "same eLMIS product"
        ):
            self.env["cdu.picking.patient.allocation"].create(
                {
                    "resolution_id": self.resolutions[1].id,
                    "stock_option_id": self.stock_30_lot_1.id,
                    "quantity_packs": 1,
                    "daily_units": 2,
                }
            )
            self.env["cdu.picking.patient.allocation"].create(
                {
                    "resolution_id": self.resolutions[1].id,
                    "stock_option_id": self.stock_30_lot_2.id,
                    "quantity_packs": 1,
                    "daily_units": 3,
                }
            )

    def test_mixed_product_coverage_must_be_confirmed_and_resets_on_change(self):
        resolution = self.resolutions[0]
        first = self._allocate(
            resolution, self.stock_30_lot_1, 1, daily_units=1
        )
        self._allocate(resolution, self.stock_60, 1, daily_units=2)
        wizard = self.env["cdu.elmis.individual.picking.wizard"].create(
            {"resolution_id": resolution.id}
        )

        with self.assertRaisesRegex(
            ValidationError, "mixed-product prescription"
        ):
            wizard.action_next()
        self.assertTrue(
            any(
                "confirm supplied days" in error
                for error in self.batch._get_picking_readiness_errors()
            )
        )

        for invalid_value in (0, -1, 1.5):
            with self.assertRaisesRegex(
                ValidationError, "whole number greater than zero"
            ):
                resolution.write(
                    {"confirmed_supplied_days": invalid_value}
                )
        resolution.confirmed_supplied_days = 30
        self.assertTrue(resolution.coverage_confirmed)
        self.assertEqual(resolution.coverage_days, 30)
        self.assertEqual(resolution.coverage_confirmed_by, self.env.user)
        self.assertTrue(resolution.coverage_confirmed_at)

        first.write({"quantity_packs": 1})
        self.assertTrue(resolution.coverage_confirmed)
        first.quantity_packs = 2
        self.assertFalse(resolution.coverage_confirmed)
        self.assertEqual(resolution.confirmed_supplied_days, 0)
        self.assertEqual(resolution.status, "draft")

    def test_bulk_group_automatically_adds_prescriptions_in_patient_order(self):
        self.picking_line.action_use_bulk_mode()
        first_group = self.env["cdu.picking.bulk.group"].create(
            {
                "name": "All patients",
                "batch_id": self.batch.id,
                "picking_line_id": self.picking_line.id,
            }
        )

        members = first_group.member_ids.sorted(
            lambda member: (member.sequence, member.id)
        )
        self.assertEqual(members.mapped("resolution_id"), self.resolutions)
        self.assertEqual(
            members.mapped("sequence"),
            self.resolutions.mapped("sequence"),
        )
        self.assertEqual(members.mapped("daily_units"), [10, 10, 10])
        self.assertEqual(members.mapped("requested_packs"), [0, 0, 0])
        self.assertTrue(first_group.distribution_stale)

        second_group = self.env["cdu.picking.bulk.group"].create(
            {
                "name": "Second component",
                "batch_id": self.batch.id,
                "picking_line_id": self.picking_line.id,
            }
        )
        self.assertEqual(
            second_group.member_ids.mapped("resolution_id"),
            self.resolutions,
        )
        with self.assertRaises(ValidationError):
            self.env["cdu.picking.bulk.member"].create(
                {
                    "group_id": second_group.id,
                    "resolution_id": self.resolutions[0].id,
                }
            )

    def test_generate_picking_list_opens_and_reuses_bulk_group_directly(self):
        self.env.user.write(
            {
                "groups_id": [
                    (
                        4,
                        self.env.ref(
                            "cdu_prescription.group_cdu_dispensing_officer"
                        ).id,
                    )
                ]
            }
        )
        self.picking_line.action_use_bulk_mode()
        service = self.env["cdu.elmis.stock.service"]

        with patch.object(
            type(service),
            "has_valid_current_user_elmis_token",
            return_value=True,
        ), patch.object(
            type(self.batch),
            "_refresh_store_stock_options",
            return_value=True,
        ):
            action = self.batch.action_generate_picking_list()

        group = self.env["cdu.picking.bulk.group"].browse(action["res_id"])
        self.assertEqual(action["res_model"], "cdu.picking.bulk.group")
        self.assertEqual(action["target"], "new")
        self.assertEqual(
            action["view_id"],
            self.env.ref("cdu_elmis.view_cdu_picking_bulk_group_form").id,
        )
        self.assertEqual(group.picking_line_id, self.picking_line)
        self.assertEqual(
            group.member_ids.mapped("resolution_id"),
            self.resolutions,
        )

        second_action = self.picking_line.action_open_allocation()
        self.assertEqual(second_action["res_id"], group.id)
        self.assertEqual(len(self.picking_line.bulk_group_ids), 1)

        overview_action = group.action_allocate_or_reallocate()
        self.assertEqual(overview_action["res_model"], "cdu.picking.line")
        self.assertEqual(overview_action["res_id"], self.picking_line.id)
        self.assertEqual(overview_action["target"], "current")
        self.assertEqual(
            overview_action["view_id"],
            self.env.ref(
                "cdu_elmis.view_cdu_picking_line_bulk_allocation_form"
            ).id,
        )

    def test_bulk_daily_units_change_recalculates_automatically(self):
        self.picking_line.action_use_bulk_mode()
        group = self.env["cdu.picking.bulk.group"].create(
            {
                "name": "Dose recalculation group",
                "batch_id": self.batch.id,
                "picking_line_id": self.picking_line.id,
            }
        )
        member = group.member_ids.filtered(
            lambda candidate: candidate.resolution_id == self.resolutions[0]
        )
        self.env["cdu.picking.bulk.lot"].create(
            {
                "group_id": group.id,
                "stock_option_id": self.stock_30_lot_1.id,
                "quantity_packs": 10,
            }
        )
        self.assertFalse(group.distribution_stale)
        self.assertEqual(member.allocated_packs, 10)
        self.assertEqual(member.allocation_status, "full")

        member.daily_units = 20
        self.assertEqual(member.requested_packs, 10)
        self.assertEqual(member.calculated_packs, 20)
        self.assertFalse(group.distribution_stale)
        self.assertEqual(member.allocated_packs, 10)
        self.assertEqual(member.coverage_days, 15)
        self.assertEqual(member.allocation_status, "partial")
        self.assertEqual(member.resolution_id.allocation_line_ids.daily_units, 20)
        self.assertEqual(group._get_distribution_errors(), [])

    def test_every_regimen_component_serves_every_prescription(self):
        self.picking_line.action_use_bulk_mode()
        component_one = self.env["cdu.picking.bulk.group"].create(
            {
                "name": "Component 1",
                "batch_id": self.batch.id,
                "picking_line_id": self.picking_line.id,
            }
        )
        component_one_stock = self._create_stock(
            "PRODUCT-E",
            "Product E 30",
            30,
            "E30-1",
            30,
        )
        self.env["cdu.picking.bulk.lot"].create(
            {
                "group_id": component_one.id,
                "stock_option_id": component_one_stock.id,
                "quantity_packs": 30,
            }
        )
        component_two_stock = self._create_stock(
            "PRODUCT-C",
            "Product C 30",
            30,
            "C30-1",
            30,
        )
        component_two = self.env["cdu.picking.bulk.group"].create(
            {
                "name": "Component 2",
                "batch_id": self.batch.id,
                "picking_line_id": self.picking_line.id,
            }
        )

        self.assertEqual(
            component_two.member_ids.mapped("resolution_id"),
            self.resolutions,
        )
        self.assertEqual(
            component_two.member_ids.mapped("required_units"),
            component_one.member_ids.mapped("required_units"),
        )

        self.env["cdu.picking.bulk.lot"].create(
            {
                "group_id": component_two.id,
                "stock_option_id": component_two_stock.id,
                "quantity_packs": 30,
            }
        )

        self.assertEqual(component_one.member_ids.mapped("allocated_packs"), [10, 10, 10])
        self.assertEqual(component_two.member_ids.mapped("allocated_packs"), [10, 10, 10])
        self.assertEqual(self.resolutions.mapped("coverage_days"), [30, 30, 30])
        self.assertEqual(self.resolutions.mapped("status"), ["full", "full", "full"])
        self.assertEqual(self.batch._get_picking_readiness_errors(), [])

    def test_overall_coverage_uses_least_covered_component(self):
        self.picking_line.action_use_bulk_mode()
        component_one = self.env["cdu.picking.bulk.group"].create(
            {
                "name": "Complete component",
                "batch_id": self.batch.id,
                "picking_line_id": self.picking_line.id,
            }
        )
        component_one_stock = self._create_stock(
            "PRODUCT-F",
            "Product F 30",
            30,
            "F30-1",
            30,
        )
        self.env["cdu.picking.bulk.lot"].create(
            {
                "group_id": component_one.id,
                "stock_option_id": component_one_stock.id,
                "quantity_packs": 30,
            }
        )
        component_two_stock = self._create_stock(
            "PRODUCT-D",
            "Product D 30",
            30,
            "D30-1",
            15,
        )
        component_two = self.env["cdu.picking.bulk.group"].create(
            {
                "name": "Short component",
                "batch_id": self.batch.id,
                "picking_line_id": self.picking_line.id,
            }
        )
        self.env["cdu.picking.bulk.lot"].create(
            {
                "group_id": component_two.id,
                "stock_option_id": component_two_stock.id,
                "quantity_packs": 15,
            }
        )

        self.assertEqual(self.resolutions.mapped("coverage_days"), [30, 15, 0])
        self.assertEqual(self.resolutions.mapped("status"), ["full", "partial", "partial"])
        self.assertEqual(component_one.member_ids.mapped("coverage_days"), [30, 30, 30])
        self.assertEqual(component_two.member_ids.mapped("coverage_days"), [30, 15, 0])

        self.batch._apply_repeat_fulfilment_results()
        balance = self.resolutions[1]._create_partial_backorder_prescriptions()
        components = balance.backorder_component_ids.sorted("sequence")
        self.assertEqual(len(components), 2)
        self.assertEqual(components.mapped("outstanding_days"), [0, 15])
        self.assertEqual(components.mapped("outstanding_units"), [0, 150])

    def test_bulk_short_stock_uses_priority_for_full_partial_and_unserved(self):
        self.picking_line.action_use_bulk_mode()
        group = self.env["cdu.picking.bulk.group"].create(
            {
                "name": "Priority group",
                "batch_id": self.batch.id,
                "picking_line_id": self.picking_line.id,
            }
        )
        self.env["cdu.picking.bulk.lot"].create(
            {
                "group_id": group.id,
                "stock_option_id": self.stock_30_lot_1.id,
                "quantity_packs": 15,
            }
        )

        self.assertEqual(self.resolutions.mapped("status"), ["full", "partial", "unserved"])
        self.assertEqual(self.resolutions.mapped("allocated_packs"), [10, 5, 0])
        self.assertEqual(group.allocated_packs, group.picked_packs)
        self.assertEqual(group.picked_packs, 15)
        self.assertEqual(self.batch._get_picking_readiness_errors(), [])

    def test_bulk_stock_changes_recalculate_automatically(self):
        self.picking_line.action_use_bulk_mode()
        group = self.env["cdu.picking.bulk.group"].create(
            {
                "name": "Automatic stock recalculation",
                "batch_id": self.batch.id,
                "picking_line_id": self.picking_line.id,
            }
        )
        lot = self.env["cdu.picking.bulk.lot"].create(
            {
                "group_id": group.id,
                "stock_option_id": self.stock_30_lot_1.id,
                "quantity_packs": 20,
            }
        )
        self.assertEqual(self.resolutions.mapped("allocated_packs"), [10, 10, 0])
        self.assertFalse(group.distribution_stale)

        lot.quantity_packs = 15
        self.assertEqual(self.resolutions.mapped("allocated_packs"), [10, 5, 0])
        self.assertFalse(group.distribution_stale)

        lot.unlink()
        self.assertEqual(self.resolutions.mapped("allocated_packs"), [0, 0, 0])
        self.assertEqual(self.resolutions.mapped("status"), ["draft", "draft", "draft"])
        self.assertTrue(group.distribution_stale)

    def test_bulk_accepts_user_selected_products_with_different_pack_sizes(self):
        self.picking_line.action_use_bulk_mode()
        group = self.env["cdu.picking.bulk.group"].create(
            {
                "name": "Trusted mixed-pack selection",
                "batch_id": self.batch.id,
                "picking_line_id": self.picking_line.id,
            }
        )
        member = group.member_ids.filtered(
            lambda candidate: candidate.resolution_id == self.resolutions[0]
        )
        self.env["cdu.picking.bulk.lot"].create(
            [
                {
                    "group_id": group.id,
                    "stock_option_id": self.stock_60.id,
                    "quantity_packs": 4,
                },
                {
                    "group_id": group.id,
                    "stock_option_id": self.stock_15.id,
                    "quantity_packs": 4,
                },
            ]
        )

        allocations = member.resolution_id.allocation_line_ids
        self.assertEqual(
            {
                allocation.selected_pack_size: allocation.quantity_packs
                for allocation in allocations
            },
            {60: 4, 15: 4},
        )
        self.assertEqual(member.required_units, 300)
        self.assertEqual(member.supplied_units, 300)
        self.assertEqual(member.coverage_days, 30)
        self.assertEqual(member.allocation_status, "full")
        self.assertEqual(member.resolution_id.distinct_product_count, 2)
        self.assertFalse(member.resolution_id.coverage_confirmation_required)
        self.assertFalse(member.resolution_id.coverage_confirmed)

    def test_bulk_minimizes_excess_for_stock_with_the_same_expiry(self):
        self.picking_line.action_use_bulk_mode()
        common_expiry = fields.Date.today() + timedelta(days=365)
        stock_200 = self._create_stock(
            "USER-SELECTED-200",
            "User Selected Pack 200",
            200,
            "PACK-200",
            2,
            expiration_date=common_expiry,
        )
        stock_150 = self._create_stock(
            "USER-SELECTED-150",
            "User Selected Pack 150",
            150,
            "PACK-150",
            2,
            expiration_date=common_expiry,
        )
        group = self.env["cdu.picking.bulk.group"].create(
            {
                "name": "Minimum excess",
                "batch_id": self.batch.id,
                "picking_line_id": self.picking_line.id,
            }
        )
        member = group.member_ids.filtered(
            lambda candidate: candidate.resolution_id == self.resolutions[0]
        )
        self.env["cdu.picking.bulk.lot"].create(
            [
                {
                    "group_id": group.id,
                    "stock_option_id": stock_200.id,
                    "quantity_packs": 2,
                },
                {
                    "group_id": group.id,
                    "stock_option_id": stock_150.id,
                    "quantity_packs": 2,
                },
            ]
        )

        allocations = member.resolution_id.allocation_line_ids
        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations.stock_option_id, stock_150)
        self.assertEqual(allocations.quantity_packs, 2)
        self.assertEqual(member.supplied_units, 300)
        self.assertEqual(member.excess_units, 0)

    def test_bulk_prefers_earliest_expiry_before_minimum_excess(self):
        self.picking_line.action_use_bulk_mode()
        stock_early_200 = self._create_stock(
            "USER-SELECTED-EARLY",
            "Early Pack 200",
            200,
            "EARLY-200",
            2,
            expiration_date=fields.Date.today() + timedelta(days=180),
        )
        stock_later_150 = self._create_stock(
            "USER-SELECTED-LATER",
            "Later Pack 150",
            150,
            "LATER-150",
            2,
            expiration_date=fields.Date.today() + timedelta(days=365),
        )
        group = self.env["cdu.picking.bulk.group"].create(
            {
                "name": "Earliest expiry",
                "batch_id": self.batch.id,
                "picking_line_id": self.picking_line.id,
            }
        )
        member = group.member_ids.filtered(
            lambda candidate: candidate.resolution_id == self.resolutions[0]
        )
        self.env["cdu.picking.bulk.lot"].create(
            [
                {
                    "group_id": group.id,
                    "stock_option_id": stock_early_200.id,
                    "quantity_packs": 2,
                },
                {
                    "group_id": group.id,
                    "stock_option_id": stock_later_150.id,
                    "quantity_packs": 2,
                },
            ]
        )

        allocations = member.resolution_id.allocation_line_ids
        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations.stock_option_id, stock_early_200)
        self.assertEqual(allocations.quantity_packs, 2)
        self.assertEqual(member.supplied_units, 400)
        self.assertEqual(member.excess_units, 100)

    def test_bulk_mode_allows_explicit_unserved_without_a_stock_group(self):
        self.picking_line.action_use_bulk_mode()
        self.resolutions.action_mark_unserved()

        self.assertEqual(self.batch._get_picking_readiness_errors(), [])

    def test_batch_wide_stock_limit_applies_across_prescriptions(self):
        self._allocate(self.resolutions[0], self.stock_30_lot_1, 15)
        with self.assertRaisesRegex(ValidationError, "exceed available packs"):
            self._allocate(self.resolutions[1], self.stock_30_lot_1, 6)

    def test_mode_switch_clears_only_affected_regimen(self):
        self._allocate(self.resolutions[0], self.stock_30_lot_1, 10)
        other_summary = self.env["cdu.batch.picking.line"].create(
            {
                "batch_id": self.batch.id,
                "unmapped_drug_name": "REGIMEN-B",
                "total_tablets": 30,
                "prescription_count": 1,
            }
        )
        other_line = self.env["cdu.picking.line"].create(
            {
                "batch_id": self.batch.id,
                "summary_line_id": other_summary.id,
                "openmrs_drug_name": "REGIMEN-B",
                "prescription_count": 1,
                "quantity_to_pick": 1,
                "allocation_mode": "individual",
            }
        )
        other_resolution = self.env["cdu.picking.patient.resolution"].create(
            {
                "batch_id": self.batch.id,
                "picking_line_id": other_line.id,
                "patient_line_id": self.patient_lines[0].id,
            }
        )
        other_allocation = self.env["cdu.picking.patient.allocation"].create(
            {
                "batch_id": self.batch.id,
                "picking_line_id": other_line.id,
                "resolution_id": other_resolution.id,
                "stock_option_id": self.stock_30_lot_2.id,
                "quantity_packs": 1,
            }
        )

        self.picking_line.action_use_bulk_mode()

        self.assertFalse(self.picking_line.patient_allocation_ids)
        self.assertTrue(other_allocation.exists())
        self.assertEqual(self.picking_line.resolution_ids.mapped("status"), ["draft"] * 3)

    def test_individual_navigation_requires_allocation_or_explicit_unserved(self):
        wizard = self.env["cdu.elmis.individual.picking.wizard"].create(
            {"resolution_id": self.resolutions[0].id}
        )
        with self.assertRaisesRegex(ValidationError, "Allocate at least one pack"):
            wizard.action_next()

        wizard.action_mark_unserved()
        action = wizard.action_next()
        self.assertEqual(self.resolutions[0].status, "unserved")
        self.assertEqual(
            self.resolutions[0].unserved_reason, "insufficient_stock"
        )
        self.assertEqual(
            action["res_model"], "cdu.elmis.individual.picking.wizard"
        )

    def test_non_admin_dispensing_officer_can_manage_allocations(self):
        officer = self.env["res.users"].create(
            {
                "name": "Picking Officer",
                "login": "picking-officer-test",
                "groups_id": [
                    (
                        6,
                        0,
                        [
                            self.env.ref(
                                "cdu_prescription.group_cdu_dispensing_officer"
                            ).id
                        ],
                    )
                ],
            }
        )
        allocation = self.env["cdu.picking.patient.allocation"].with_user(
            officer
        ).create(
            {
                "batch_id": self.batch.id,
                "picking_line_id": self.picking_line.id,
                "resolution_id": self.resolutions[0].id,
                "stock_option_id": self.stock_30_lot_1.id,
                "quantity_packs": 10,
                "allocation_method": "individual",
            }
        )
        self.assertEqual(allocation.quantity_packs, 10)
        allocation.with_user(officer).quantity_packs = 9
        self.assertEqual(allocation.quantity_packs, 9)
        allocation.with_user(officer).daily_units = 12
        self.assertEqual(allocation.daily_units, 12)
        self.assertEqual(allocation.daily_units_changed_by, officer)

    def test_stock_refresh_preserves_referenced_lot_and_updates_availability(self):
        allocation = self._allocate(
            self.resolutions[0], self.stock_30_lot_1, 10
        )
        original_option_id = allocation.stock_option_id.id
        payload = [
            {
                "orderable": "PRODUCT-A",
                "orderableName": "Product A 30",
                "packSize": 30,
                "stockCards": [
                    {
                        "lot": "A30-1",
                        "stockOnHand": 450,
                        "expirationDate": "2028-12-31",
                    }
                ],
            }
        ]

        self.batch._replace_store_stock_options(
            payload, facility_code="TEST-CDU", program_code="TEST-PROGRAM"
        )

        self.assertEqual(allocation.stock_option_id.id, original_option_id)
        self.assertEqual(allocation.stock_option_id.stock_on_hand, 15)

    def test_dispensing_prefill_uses_exact_patient_lot_quantities_once(self):
        resolution = self.resolutions[0]
        self._allocate(resolution, self.stock_30_lot_1, 2)
        self._allocate(resolution, self.stock_60, 4)
        dispense = self.env["cdu.dispense"].with_context(
            cdu_skip_auto_refresh_production_stock=True
        ).create({"prescription_id": resolution.prescription_id.id})

        self.assertEqual(len(dispense.stock_selection_ids), 2)
        self.assertEqual(
            sorted(dispense.stock_selection_ids.mapped("quantity_dispensed")),
            [2, 4],
        )
        self.assertEqual(
            set(dispense.stock_selection_ids.mapped("selected_pack_size")),
            {30, 60},
        )

    def test_confirmation_requeues_unserved_and_locks_served_allocations(self):
        self.env.user.write(
            {
                "groups_id": [
                    (
                        4,
                        self.env.ref(
                            "cdu_prescription.group_cdu_dispensing_officer"
                        ).id,
                    )
                ]
            }
        )
        allocation = self._allocate(
            self.resolutions[0], self.stock_30_lot_1, 10
        )
        self.resolutions[1:].action_mark_unserved()
        params = self.env["ir.config_parameter"].sudo()
        values = {
            "cdu.elmis.cdu_store_facility_code": "STORE",
            "cdu.elmis.cdu_production_floor_facility_code": "FLOOR",
            "cdu.elmis.default_program_code": "PROGRAM",
            "cdu.elmis.picking_debit_reason_name": "DEBIT",
            "cdu.elmis.picking_credit_reason_name": "CREDIT",
        }
        for key, value in values.items():
            params.set_param(key, value)
        service = self.env["cdu.elmis.stock.service"]
        with patch.object(
            type(service),
            "has_valid_current_user_elmis_token",
            return_value=True,
        ), patch.object(
            type(service), "post_internal_stock_event", return_value={}
        ), patch.object(
            type(service), "invalidate_stock_cache", return_value=True
        ):
            action = self.picking_line.action_confirm_picking()

        self.assertEqual(action["res_model"], "cdu.batch")
        self.assertEqual(action["res_id"], self.batch.id)
        self.assertEqual(action["target"], "current")
        self.assertEqual(
            action["views"],
            [
                (
                    self.env.ref("cdu_prescription.view_cdu_batch_form").id,
                    "form",
                )
            ],
        )
        self.assertEqual(self.resolutions[0].prescription_id.state, "awaiting_dispensing")
        for prescription in self.resolutions[1:].mapped("prescription_id"):
            self.assertEqual(prescription.state, "awaiting_batching")
            self.assertFalse(prescription.batch_id)
        self.assertTrue(allocation.locked)
        self.assertTrue(self.resolutions[0].locked)
        with self.assertRaises(UserError):
            allocation.quantity_packs = 9
        with self.assertRaises(UserError):
            allocation.daily_units = 12

    def test_confirmation_creates_linked_backorder_for_partial_supply(self):
        self.env.user.write(
            {
                "groups_id": [
                    (
                        4,
                        self.env.ref(
                            "cdu_prescription.group_cdu_dispensing_officer"
                        ).id,
                    ),
                    (
                        4,
                        self.env.ref(
                            "cdu_prescription.group_cdu_data_clerk"
                        ).id,
                    ),
                ]
            }
        )
        source_prescription = self.resolutions[0].prescription_id
        self._allocate(
            self.resolutions[0],
            self.stock_30_lot_1,
            5,
            daily_units=10,
        )
        self.resolutions[1:].action_mark_unserved()
        params = self.env["ir.config_parameter"].sudo()
        for key, value in {
            "cdu.elmis.cdu_store_facility_code": "STORE",
            "cdu.elmis.cdu_production_floor_facility_code": "FLOOR",
            "cdu.elmis.default_program_code": "PROGRAM",
            "cdu.elmis.picking_debit_reason_name": "DEBIT",
            "cdu.elmis.picking_credit_reason_name": "CREDIT",
        }.items():
            params.set_param(key, value)
        service = self.env["cdu.elmis.stock.service"]
        with patch.object(
            type(service),
            "has_valid_current_user_elmis_token",
            return_value=True,
        ), patch.object(
            type(service), "post_internal_stock_event", return_value={}
        ), patch.object(
            type(service), "invalidate_stock_cache", return_value=True
        ):
            self.picking_line.action_confirm_picking()

        balance = source_prescription.backorder_prescription_ids
        self.assertEqual(len(balance), 1)
        self.assertTrue(balance.is_backorder)
        self.assertEqual(balance.state, "awaiting_verification")
        self.assertEqual(balance.backorder_source_prescription_id, source_prescription)
        self.assertEqual(balance.backorder_root_prescription_id, source_prescription)
        self.assertEqual(balance.backorder_created_from_batch_id, self.batch)
        self.assertFalse(balance.batch_id)
        self.assertEqual(balance.backorder_required_days, 15)
        self.assertEqual(balance.repeat_days, 15)
        self.assertEqual(balance.remaining_days_supply, 15)
        self.assertEqual(balance.cdu_days_supply, 15)
        self.assertEqual(
            balance.next_drug_pickup_date,
            self.pickup_date + timedelta(days=15),
        )
        self.assertEqual(source_prescription.state, "awaiting_dispensing")
        self.assertEqual(source_prescription.remaining_days_supply, 15)
        component = balance.backorder_component_ids
        self.assertEqual(len(component), 1)
        self.assertEqual(component.target_days, 30)
        self.assertEqual(component.supplied_days, 15)
        self.assertEqual(component.outstanding_days, 15)
        self.assertEqual(component.outstanding_units, 150)

        self.resolutions[0]._create_partial_backorder_prescriptions()
        self.assertEqual(len(source_prescription.backorder_prescription_ids), 1)

        balance.action_mark_patient_verified()
        self.assertEqual(balance.state, "awaiting_validation")
        balance.action_mark_medicine_validated()
        self.assertEqual(balance.state, "awaiting_batching")

        balance_batch = self.env["cdu.batch"].create({"state": "draft"})
        balance.batch_id = balance_batch
        patient_values, summary_values = balance_batch._prepare_picking_line_values()
        self.assertEqual(len(patient_values), 1)
        self.assertEqual(patient_values[0]["prescription_id"], balance.id)
        self.assertEqual(patient_values[0]["effective_repeat_days"], 15)
        self.assertEqual(patient_values[0]["required_units"], 15)
        self.assertEqual(len(summary_values), 1)

    def test_confirmed_legacy_aggregate_is_backfilled_and_locked_without_events(self):
        fulfilment = self.env["cdu.picking.fulfilment.line"].create(
            {
                "batch_id": self.batch.id,
                "picking_line_id": self.picking_line.id,
                "selected_stock_option_id": self.stock_30_lot_1.id,
                "quantity_picked": 12,
            }
        )
        fulfilment.quantity_picked = 12
        self.batch.picking_confirmed_at = fields.Datetime.now()

        self.batch._migrate_legacy_picking_allocations()

        self.assertEqual(
            sum(self.batch.patient_allocation_ids.mapped("quantity_packs")), 12
        )
        self.assertTrue(all(self.batch.patient_allocation_ids.mapped("legacy")))
        self.assertTrue(all(self.batch.patient_allocation_ids.mapped("locked")))
        self.assertEqual(
            self.resolutions.mapped("status"), ["full", "partial", "unserved"]
        )
