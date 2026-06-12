from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class CduBox(models.Model):
    _name = "cdu.box"
    _description = "CDU Box"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc"

    name = fields.Char(default="/", copy=False, readonly=True, tracking=True)
    collection_point_id = fields.Many2one(
        "cdu.collection.point",
        string="Collection Point",
        tracking=True,
    )
    next_drug_pickup_date = fields.Date(string="Next Drug Pickup Date", tracking=True)
    max_parcels = fields.Integer(default=20, required=True, tracking=True)
    line_ids = fields.One2many("cdu.box.line", "box_id", string="Parcels")
    parcel_count = fields.Integer(compute="_compute_parcel_count", store=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("confirmed", "Confirmed"),
        ],
        default="draft",
        tracking=True,
    )
    dispatch_status = fields.Selection(
        [
            ("pending", "Pending Dispatch"),
            ("dispatched", "Dispatched"),
        ],
        default="pending",
        required=True,
        tracking=True,
    )
    confirmed_by = fields.Many2one("res.users", readonly=True)
    confirmed_at = fields.Datetime(readonly=True)
    consumption_log_id = fields.Many2one(
        "cdu.elmis.api.log",
        string="Consumption Event Log",
        readonly=True,
        copy=False,
    )
    consumption_posted_at = fields.Datetime(string="Consumption Posted At", readonly=True)
    dispatch_ready_at = fields.Datetime(string="Ready for Dispatch At", readonly=True)
    dispatched_by = fields.Many2one("res.users", readonly=True)
    dispatched_at = fields.Datetime(readonly=True)
    dispatch_notes = fields.Text(string="Dispatch Notes", tracking=True)
    collect_go_status = fields.Selection(
        [
            ("not_submitted", "Not Submitted"),
            ("submitted", "Submitted"),
            ("processed", "Processed"),
            ("failed", "Failed"),
            ("retry_limit_reached", "Retry Limit Reached"),
        ],
        string="Collect-and-Go Status",
        default="not_submitted",
        required=True,
        tracking=True,
        index=True,
    )
    collect_go_reference_guid = fields.Char(string="Collect-and-Go Reference GUID", readonly=True, copy=False)
    collect_go_last_log_id = fields.Many2one(
        "cdu.collect.go.api.log",
        string="Last Collect-and-Go Log",
        readonly=True,
        copy=False,
    )
    collect_go_log_ids = fields.One2many(
        "cdu.collect.go.api.log",
        "box_id",
        string="Collect-and-Go Logs",
        readonly=True,
    )
    collect_go_submitted_at = fields.Datetime(string="Submitted to Collect-and-Go At", readonly=True)
    collect_go_processed_at = fields.Datetime(string="Processed by Collect-and-Go At", readonly=True)
    collect_go_error = fields.Char(string="Collect-and-Go Error", readonly=True)
    collect_go_last_status_poll_at = fields.Datetime(
        string="Last Status Poll At",
        readonly=True,
        copy=False,
    )
    collect_go_latest_parcel_status_summary = fields.Char(
        string="Latest Parcel Status Summary",
        readonly=True,
        copy=False,
    )

    @api.model
    def create(self, vals):
        if vals.get("name", "/") == "/":
            vals["name"] = self.env["ir.sequence"].next_by_code("cdu.box") or "/"
        return super().create(vals)

    @api.depends("line_ids")
    def _compute_parcel_count(self):
        for box in self:
            box.parcel_count = len(box.line_ids)

    def _ensure_boxing_access(self):
        if not (
            self.env.user.has_group("cdu_prescription.group_cdu_dispensing_officer")
            or self.env.user.has_group("cdu_prescription.group_cdu_admin")
        ):
            raise AccessError(_("Only CDU dispensing officers can manage boxing."))

    def _get_boxing_validation_errors(self):
        self.ensure_one()
        errors = []
        if not self.line_ids:
            errors.append(_("Add at least one parcel to the box."))
        if self.parcel_count > self.max_parcels:
            errors.append(
                _("This box has %(count)s parcels; the maximum allowed is %(max)s.")
                % {"count": self.parcel_count, "max": self.max_parcels}
            )

        collection_points = self.line_ids.mapped("collection_point_id")
        pickup_dates = set(self.line_ids.mapped("next_drug_pickup_date"))
        if len(collection_points) > 1:
            errors.append(_("All parcels in a box must have the same collection point."))
        if len(pickup_dates) > 1:
            errors.append(_("All parcels in a box must have the same next drug pickup date."))
        return errors

    def _sync_from_lines(self):
        for box in self:
            if box.state != "draft" or not box.line_ids:
                continue
            first_line = box.line_ids[0]
            values = {}
            if not box.collection_point_id:
                values["collection_point_id"] = first_line.collection_point_id.id
            if not box.next_drug_pickup_date:
                values["next_drug_pickup_date"] = first_line.next_drug_pickup_date
            if values:
                box.write(values)

    def action_load_eligible_parcels(self):
        self._ensure_boxing_access()
        BoxLine = self.env["cdu.box.line"]
        BaggingQa = self.env["cdu.bagging.qa"]
        for box in self:
            if box.state != "draft":
                raise UserError(_("Only draft boxes can load eligible parcels."))
            if not box.line_ids and not (box.collection_point_id and box.next_drug_pickup_date):
                raise UserError(
                    _(
                        "Add one parcel first, or set a collection point and next drug pickup date, "
                        "so the system knows which eligible parcels to load."
                    )
                )

            box._sync_from_lines()
            if not (box.collection_point_id and box.next_drug_pickup_date):
                raise UserError(_("The box collection point and pickup date could not be determined."))

            remaining_capacity = box.max_parcels - box.parcel_count
            if remaining_capacity <= 0:
                raise UserError(_("This box is already at its maximum parcel capacity."))

            already_boxed_ids = BoxLine.search([]).mapped("bagging_qa_id").ids
            domain = [
                ("state", "=", "confirmed"),
                ("prescription_id.state", "=", "awaiting_boxing"),
                ("collection_point_id", "=", box.collection_point_id.id),
                ("next_drug_pickup_date", "=", box.next_drug_pickup_date),
                ("id", "not in", already_boxed_ids),
            ]
            eligible = BaggingQa.search(
                domain,
                order="parcel_reference asc, patient_name asc, id asc",
                limit=remaining_capacity,
            )
            existing_ids = set(box.line_ids.mapped("bagging_qa_id").ids)
            new_records = eligible.filtered(lambda qa: qa.id not in existing_ids)
            if not new_records:
                raise UserError(_("No additional eligible parcels were found for this box."))

            BoxLine.create(
                [
                    {
                        "box_id": box.id,
                        "bagging_qa_id": qa.id,
                    }
                    for qa in new_records
                ]
            )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Eligible parcels loaded"),
                "message": _("Matching parcels were added to the box. Remove any parcels that should not be included."),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    def action_confirm_box(self):
        self._ensure_boxing_access()
        service = self.env["cdu.elmis.stock.service"]
        if not service.has_valid_current_user_elmis_token():
            return service.action_open_elmis_auth_wizard(
                batch=self[:1].line_ids.mapped("prescription_id.batch_id")[:1]
            )
        moved_count = 0
        for box in self:
            if box.state == "confirmed":
                raise UserError(_("This box has already been confirmed."))
            errors = box._get_boxing_validation_errors()
            if errors:
                raise ValidationError(_("Box cannot be confirmed yet:\n\n%s") % "\n".join(errors))
            now = fields.Datetime.now()
            prescriptions = box.line_ids.mapped("prescription_id").filtered(
                lambda prescription: prescription.state == "awaiting_boxing"
            )
            consumption_log = box._submit_consumption_event()
            box.write(
                {
                    "state": "confirmed",
                    "confirmed_by": self.env.user.id,
                    "confirmed_at": now,
                    "consumption_log_id": consumption_log.id,
                    "consumption_posted_at": now,
                    "dispatch_ready_at": now,
                }
            )
            prescriptions.write({"state": "awaiting_dispatch"})
            moved_count += len(prescriptions)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Box confirmed"),
                "message": _(
                    "Production Floor stock was consumed in eLMIS. %(count)s parcel(s) moved to Awaiting Dispatch."
                )
                % {"count": moved_count},
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    def action_confirm_dispatch_handover(self):
        self._ensure_boxing_access()
        submitted_count = 0
        for box in self:
            if box.state != "confirmed":
                raise UserError(_("Confirm the box before dispatch handover."))
            if box.dispatch_status == "dispatched":
                raise UserError(_("Dispatch handover has already been confirmed for %s.") % box.name)
            if box.collect_go_status != "not_submitted":
                raise UserError(
                    _(
                        "%s already has a Collect-and-Go submission status. "
                        "Use Retry Collect-and-Go if the previous submission failed."
                    )
                    % box.name
                )
            prescriptions = box.line_ids.mapped("prescription_id")
            awaiting_dispatch = prescriptions.filtered(
                lambda prescription: prescription.state == "awaiting_dispatch"
            )
            if not awaiting_dispatch:
                raise UserError(_("No prescriptions in this box are awaiting dispatch."))
            self.env["cdu.collect.go.service"].submit_box_update_parcel(box)
            submitted_count += 1
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Submitted to Collect-and-Go"),
                "message": _(
                    "%(count)s box update(s) were accepted by the Collect-and-Go bridge. "
                    "Click Check Collect-and-Go Response to confirm final processing."
                )
                % {"count": submitted_count},
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    def action_retry_collect_go_submission(self):
        self._ensure_boxing_access()
        retried_count = 0
        for box in self:
            self.env["cdu.collect.go.service"].retry_box_update_parcel(box)
            retried_count += 1
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Collect-and-Go retry submitted"),
                "message": _(
                    "%(count)s box update(s) were re-submitted to Collect-and-Go. "
                    "Click Check Collect-and-Go Response to confirm final processing."
                )
                % {"count": retried_count},
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    def action_check_collect_go_response(self):
        self._ensure_boxing_access()
        results = []
        service = self.env["cdu.collect.go.service"]
        for box in self:
            results.append(service.poll_box_message(box, raise_on_error=False))

        processed_count = sum(1 for result in results if result.get("state") == "processed")
        failed_count = sum(1 for result in results if result.get("state") == "failed")
        pending_count = sum(1 for result in results if result.get("state") == "not_ready")

        if failed_count:
            title = _("Collect-and-Go processing failed")
            notification_type = "danger"
            message = _(
                "%(failed)s box(es) failed during Collect-and-Go processing. "
                "The getMessage response was saved in the Collect-and-Go logs."
            ) % {"failed": failed_count}
        elif pending_count:
            title = _("Collect-and-Go response pending")
            notification_type = "warning"
            message = _(
                "%(pending)s box(es) are still waiting for Collect-and-Go processing. "
                "The getMessage response was saved in the Collect-and-Go logs."
            ) % {"pending": pending_count}
        else:
            title = _("Collect-and-Go processed")
            notification_type = "success"
            message = _("%(count)s box(es) were processed and marked as dispatched.") % {
                "count": processed_count
            }

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": title,
                "message": message,
                "type": notification_type,
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    def action_poll_collect_go_parcel_status(self):
        self._ensure_boxing_access()
        result = self.env["cdu.collect.go.service"].poll_parcel_status_updates(boxes=self)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Collect-and-Go status poll complete"),
                "message": _(
                    "%(matched)s parcel status update(s) matched CDU parcels. "
                    "%(received)s status update(s) were received."
                )
                % {
                    "matched": result.get("matched_count", 0),
                    "received": result.get("received_count", 0),
                },
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    @api.model
    def cron_poll_collect_go_parcel_status(self):
        return self.env["cdu.collect.go.service"].cron_poll_parcel_status_updates()

    def _mark_collect_go_processed(self, log):
        self.ensure_one()
        prescriptions = self.line_ids.mapped("prescription_id")
        awaiting_dispatch = prescriptions.filtered(
            lambda prescription: prescription.state == "awaiting_dispatch"
        )
        now = fields.Datetime.now()
        self.write(
            {
                "collect_go_status": "processed",
                "collect_go_last_log_id": log.id,
                "collect_go_processed_at": now,
                "collect_go_error": False,
                "dispatch_status": "dispatched",
                "dispatched_by": self.env.user.id,
                "dispatched_at": now,
            }
        )
        awaiting_dispatch.write({"state": "dispatched"})

    def _update_collect_go_status_summary(self):
        for box in self:
            status_counts = {}
            for line in box.line_ids:
                status = line.collect_go_parcel_status or _("Unknown")
                status_counts[status] = status_counts.get(status, 0) + 1
            summary = ", ".join(
                "%s: %s" % (status, count)
                for status, count in sorted(status_counts.items())
            )
            box.write(
                {
                    "collect_go_last_status_poll_at": fields.Datetime.now(),
                    "collect_go_latest_parcel_status_summary": summary,
                }
            )

    def _submit_consumption_event(self):
        self.ensure_one()
        params = self.env["ir.config_parameter"].sudo()
        production_facility_code = params.get_param(
            "cdu.elmis.cdu_production_floor_facility_code"
        )
        program_code = params.get_param("cdu.elmis.default_program_code")
        consumption_reason = params.get_param("cdu.elmis.consumption_reason_name")

        missing = []
        if not production_facility_code:
            missing.append(_("CDU Production Floor Facility Code"))
        if not program_code:
            missing.append(_("Default Program Code"))
        if not consumption_reason:
            missing.append(_("Consumption Reason"))
        if missing:
            raise UserError(_("Missing eLMIS configuration: %s") % ", ".join(missing))

        batch = self.line_ids.mapped("prescription_id.batch_id")[:1]
        return self.env["cdu.elmis.stock.service"].post_internal_stock_event(
            facility_code=production_facility_code,
            program_code=program_code,
            items=self._build_consumption_stock_event_items(consumption_reason),
            call_type="CONSUMPTION",
            batch=batch,
            box=self,
        )

    def _build_consumption_stock_event_items(self, reason_name):
        self.ensure_one()
        grouped = {}
        selections = self.line_ids.mapped("bagging_qa_id.dispense_id.stock_selection_ids")
        if not selections:
            raise UserError(_("No dispense stock selections were found for this box."))

        for line in selections:
            label = line.openmrs_drug_name or line.dispense_id.name
            if line.dispense_id.state != "confirmed":
                raise UserError(_("%s: dispensing must be confirmed before boxing.") % label)
            if not (line.selected_orderable_id or line.selected_orderable_code):
                raise UserError(_("%s: eLMIS product is missing.") % label)
            if line.quantity_dispensed <= 0:
                raise UserError(_("%s: dispensed quantity must be greater than zero.") % label)

            key = (
                line.selected_orderable_id or line.selected_orderable_code,
                line.selected_lot_id or line.selected_lot or "",
            )
            if key not in grouped:
                grouped[key] = {
                    "orderable": line.selected_orderable_code,
                    "orderableId": line.selected_orderable_id,
                    "quantity": 0,
                    "reason": reason_name,
                    "occurredDate": fields.Date.to_string(fields.Date.today()),
                }
                if line.selected_lot_id:
                    grouped[key]["lotId"] = line.selected_lot_id
                if line.selected_lot:
                    grouped[key]["lot"] = line.selected_lot
            grouped[key]["quantity"] += line.quantity_dispensed
        return list(grouped.values())


class CduBoxLine(models.Model):
    _name = "cdu.box.line"
    _description = "CDU Box Parcel Line"
    _order = "box_id, parcel_reference, id"

    box_id = fields.Many2one(
        "cdu.box",
        required=True,
        ondelete="cascade",
        index=True,
    )
    bagging_qa_id = fields.Many2one(
        "cdu.bagging.qa",
        string="Parcel",
        required=True,
        domain="[('state', '=', 'confirmed'), ('prescription_id.state', '=', 'awaiting_boxing')]",
    )
    prescription_id = fields.Many2one(
        "cdu.prescription",
        related="bagging_qa_id.prescription_id",
        store=True,
        readonly=True,
    )
    parcel_reference = fields.Char(
        related="bagging_qa_id.parcel_reference",
        store=True,
        readonly=True,
    )
    patient_name = fields.Char(
        related="bagging_qa_id.patient_name",
        store=True,
        readonly=True,
    )
    collection_point_id = fields.Many2one(
        "cdu.collection.point",
        related="bagging_qa_id.collection_point_id",
        store=True,
        readonly=True,
    )
    next_drug_pickup_date = fields.Date(
        related="bagging_qa_id.next_drug_pickup_date",
        store=True,
        readonly=True,
    )
    collect_go_parcel_status_type = fields.Integer(
        string="Collect-and-Go Status Type",
        readonly=True,
        copy=False,
    )
    collect_go_parcel_status = fields.Char(
        string="Collect-and-Go Status",
        readonly=True,
        copy=False,
    )
    collect_go_status_received_at = fields.Datetime(
        string="Collect-and-Go Status Received At",
        readonly=True,
        copy=False,
    )
    collect_go_status_date = fields.Char(
        string="Collect-and-Go Status Date",
        readonly=True,
        copy=False,
    )
    collect_go_status_raw_json = fields.Text(
        string="Collect-and-Go Raw Status",
        readonly=True,
        copy=False,
    )

    _sql_constraints = [
        (
            "unique_bagging_qa_box_line",
            "unique(bagging_qa_id)",
            "This parcel has already been added to a box.",
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        box_ids = [vals.get("box_id") for vals in vals_list if vals.get("box_id")]
        if self.env["cdu.box"].browse(box_ids).filtered(lambda box: box.state == "confirmed"):
            raise UserError(_("Parcels cannot be added to a confirmed box."))
        records = super().create(vals_list)
        records.mapped("box_id")._sync_from_lines()
        return records

    def write(self, vals):
        status_update_fields = {
            "collect_go_parcel_status_type",
            "collect_go_parcel_status",
            "collect_go_status_received_at",
            "collect_go_status_date",
            "collect_go_status_raw_json",
        }
        if (
            self.mapped("box_id").filtered(lambda box: box.state == "confirmed")
            and set(vals) - status_update_fields
        ):
            raise UserError(_("Parcels cannot be changed on a confirmed box."))
        result = super().write(vals)
        self.mapped("box_id")._sync_from_lines()
        return result

    def unlink(self):
        boxes = self.mapped("box_id")
        if boxes.filtered(lambda box: box.state == "confirmed"):
            raise UserError(_("Parcels cannot be removed from a confirmed box."))
        result = super().unlink()
        boxes._sync_from_lines()
        return result
