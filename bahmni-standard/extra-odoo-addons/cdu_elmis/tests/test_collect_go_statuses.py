import json
from datetime import datetime, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests.common import TransactionCase, tagged

from ..models.collect_go_status import parse_status_date, status_label


def message(*parcels):
    """One getParcelStatus message, shaped like Collect & Go's real responses."""
    return {
        "method": "UpdateParcelStatus",
        "reference": "00000000-0000-4000-8000-000000000001",
        "type": 0,
        "body": json.dumps({"Parcels": list(parcels)}),
    }


def parcel(reference, *tracking, parcel_status_type=0):
    return {
        "ExternalReference": reference,
        "ParcelStatusType": parcel_status_type,
        "LocationID": "501",
        "TrackingStatuses": [
            {"Date": date, "LocationID": "501", "TrackingStatusType": code} for code, date in tracking
        ],
    }


@tagged("post_install", "-at_install", "cdu_collect_go_status")
class TestCollectGoStatuses(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.service = cls.env["cdu.collect.go.service"]
        facility = cls.env["cdu.facility"].create({"name": "C&G Status Facility", "code": "TEST-CG-FACILITY"})
        cls.collection_point = cls.env["cdu.collection.point"].create(
            {"name": "C&G Status Pickup Point", "code": "TEST-CG-PUP"}
        )
        patient = cls.env["res.partner"].create(
            {"name": "C&G Status Patient", "customer_rank": 1, "cdu_eregister_id": "TEST-CG-EREGISTER"}
        )
        today = fields.Date.today()
        cls.prescription = cls.env["cdu.prescription"].create(
            {
                "facility_id": facility.id,
                "facility_name": facility.name,
                "facility_code": facility.code,
                "prescription_date": today,
                "patient_id": patient.id,
                "patient_identifier": patient.cdu_eregister_id,
                "patient_first_name": patient.name,
                "regimen_prescribed_raw": "TEST-CG-REGIMEN",
                "dosage_instructions": "Take one tablet daily.",
                "drug_pickup_point_raw": cls.collection_point.name,
                "collection_point_id": cls.collection_point.id,
                "next_drug_pickup_date": today + timedelta(days=30),
                "state": "awaiting_boxing",
            }
        )
        dispense = cls.env["cdu.dispense"].with_context(cdu_skip_auto_refresh_production_stock=True).create(
            {"prescription_id": cls.prescription.id, "state": "confirmed"}
        )
        qa = cls.env["cdu.bagging.qa"].create(
            {
                "prescription_id": cls.prescription.id,
                "dispense_id": dispense.id,
                "state": "confirmed",
                "parcel_reference": "TEST-CG-PARCEL-1",
            }
        )
        cls.prescription.write({"state": "awaiting_boxing"})
        box = cls.env["cdu.box"].create({"collection_point_id": cls.collection_point.id})
        cls.line = cls.env["cdu.box.line"].create({"box_id": box.id, "bagging_qa_id": qa.id})
        cls.prescription.write({"state": "dispatched"})

    def _receive(self, *tracking, reference="TEST-CG-PARCEL-1"):
        items = self.service._extract_parcel_status_items([message(parcel(reference, *tracking))])
        return self.service._apply_parcel_status_items(items)

    # ---------------------------------------------------------------- parsing
    def test_collect_go_times_are_local_maseru_time(self):
        self.assertEqual(parse_status_date("2026-10-10T12:45:16.3"), datetime(2026, 10, 10, 10, 45, 16, 300000))
        self.assertEqual(parse_status_date("2026-10-10T12:45:16.287Z"), datetime(2026, 10, 10, 12, 45, 16, 287000))
        self.assertEqual(parse_status_date("2026-10-10T12:45:16+02:00"), datetime(2026, 10, 10, 10, 45, 16))
        self.assertIsNone(parse_status_date("yesterday"))
        self.assertEqual(status_label(12), "Collected by Customer")
        self.assertEqual(status_label(99), "Unknown status 99")

    def test_parcels_are_read_from_inside_the_messages(self):
        payload = [
            message(parcel("B1", (5, "2026-10-10T08:00:00"))),
            message(parcel("B2", (12, "2026-10-10T09:00:00")), parcel("B3", (7, "2026-10-10T09:30:00"))),
        ]
        refs = [item["ExternalReference"] for item in self.service._extract_parcel_status_items(payload)]
        self.assertEqual(refs, ["B1", "B2", "B3"])

    def test_the_latest_tracking_status_wins_over_parcel_status_and_list_order(self):
        item = parcel(
            "B1",
            (12, "2026-10-10T15:00:00"),
            (5, "2026-10-10T08:00:00"),
            parcel_status_type=4,
        )
        self.assertEqual(self.service._parcel_status_type(item), 12)
        self.assertEqual(self.service._parcel_status_label(item), "Collected by Customer")
        no_history = parcel("B2", parcel_status_type=0)
        self.assertEqual(self.service._parcel_status_type(no_history), 0)

    # --------------------------------------------------------------- outcomes
    def test_in_transit_and_at_pickup_point_are_information_only(self):
        self.assertEqual(self._receive((5, "2026-10-10T08:00:00")), 1)
        self.assertEqual(self.prescription.state, "dispatched")
        self._receive((14, "2026-10-10T09:00:00"))
        self.assertEqual(self.line.collect_go_parcel_status, "Delivered to PCU (Confirmed)")
        self.assertEqual(self.prescription.collect_go_parcel_status, "Delivered to PCU (Confirmed)")
        self.assertEqual(self.prescription.state, "dispatched")

    def test_collection_moves_the_prescription_on_and_is_final(self):
        self._receive((12, "2026-10-10T12:45:16.3"))
        self.assertEqual(self.prescription.state, "collected")
        self.assertEqual(self.prescription.collected_at, datetime(2026, 10, 10, 10, 45, 16))
        self.assertIn("Collected by the patient", self.prescription.message_ids[:1].body)

        self._receive((11, "2026-10-11T08:00:00"))
        self.assertEqual(self.prescription.state, "collected")

    def test_only_a_confirmed_return_moves_the_prescription(self):
        self._receive((10, "2026-10-10T08:00:00"))
        self.assertEqual(self.prescription.state, "dispatched")
        self.assertEqual(self.prescription.collect_go_parcel_status, "Returned to CDU (Unconfirmed)")

        self._receive((11, "2026-10-10T09:00:00"))
        self.assertEqual(self.prescription.state, "returned_uncollected")
        self.assertEqual(self.prescription.returned_uncollected_at, datetime(2026, 10, 10, 7, 0, 0))

    def test_an_older_status_never_replaces_a_newer_one(self):
        self._receive((7, "2026-10-10T12:00:00"))
        self._receive((5, "2026-10-10T08:00:00"))
        self.assertEqual(self.line.collect_go_parcel_status_type, 7)

    def test_wrong_location_and_ready_for_cdu_raise_one_activity_each(self):
        self._receive((8, "2026-10-10T08:00:00"))
        self._receive((9, "2026-10-10T09:00:00"))
        self._receive((15, "2026-10-10T10:00:00"))
        summaries = self.prescription.activity_ids.mapped("summary")
        self.assertEqual(
            sorted(summaries),
            ["Parcel delivered to the wrong location", "Uncollected parcel ready for the CDU to fetch"],
        )
        self.assertEqual(self.prescription.state, "dispatched")

    def test_a_repeated_status_is_not_acted_on_again(self):
        # getParcelStatus is a snapshot, so every poll repeats the latest status.
        self.assertEqual(self._receive((15, "2026-10-10T10:00:00")), 1)
        received_at = self.line.collect_go_status_received_at
        self.prescription.activity_ids.action_feedback(feedback="Driver sent to fetch it.")
        notes = len(self.prescription.message_ids)

        with patch.object(fields.Datetime, "now", return_value=received_at + timedelta(minutes=5)):
            self.assertEqual(self._receive((15, "2026-10-10T10:00:00")), 1)
        self.assertFalse(self.prescription.activity_ids)
        self.assertEqual(len(self.prescription.message_ids), notes)
        self.assertEqual(self.line.collect_go_status_received_at, received_at)

        # A new status is still acted on.
        self._receive((8, "2026-10-10T11:00:00"))
        self.assertEqual(self.prescription.activity_ids.summary, "Parcel delivered to the wrong location")

    def test_returned_to_vendor_means_back_at_the_cdu(self):
        self._receive((16, "2026-10-10T11:00:00"))
        self.assertEqual(self.prescription.state, "returned_uncollected")
        self.assertEqual(self.prescription.collect_go_parcel_status, "Returned to Vendor")
        self.assertEqual(self.prescription.returned_uncollected_at, datetime(2026, 10, 10, 9, 0, 0))

    def test_the_scheduled_poll_handles_a_real_response(self):
        response = {"status_code": 200, "body": json.dumps([message(parcel("TEST-CG-PARCEL-1", (12, "2026-10-10T12:00:00")))])}
        self.env["ir.config_parameter"].sudo().set_param("cdu.collect_go.base_url", "https://collect-go.test/api")
        with patch.object(type(self.service), "_http_request", lambda _self, method, endpoint, payload=None: response):
            result = self.service.poll_parcel_status_updates(raise_on_error=True)
        self.assertEqual((result["received_count"], result["matched_count"]), (1, 1))
        self.assertEqual(self.prescription.state, "collected")
