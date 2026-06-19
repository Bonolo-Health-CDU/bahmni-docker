from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.osv import expression


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

    def write(self, vals):
        rule_fields = {"collection_point_id", "next_drug_pickup_date"}
        changing_rule = rule_fields.intersection(vals)
        if changing_rule and not self.env.context.get("skip_box_rule_change_guard"):
            for box in self:
                if box.state == "confirmed":
                    raise UserError(_("Box rules cannot be changed after confirmation."))
                changed_values = {}
                if "collection_point_id" in changing_rule:
                    new_collection_point_id = vals["collection_point_id"] or False
                    if new_collection_point_id != (box.collection_point_id.id or False):
                        changed_values["collection_point_id"] = new_collection_point_id
                if "next_drug_pickup_date" in changing_rule:
                    new_date = fields.Date.to_date(vals["next_drug_pickup_date"]) or False
                    if new_date != (box.next_drug_pickup_date or False):
                        changed_values["next_drug_pickup_date"] = new_date
                if changed_values and box.line_ids:
                    raise UserError(
                        _(
                            "Remove existing parcels before changing the box collection point "
                            "or next drug pickup date."
                        )
                    )
        return super().write(vals)

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
        duplicate_parcels = self._get_duplicate_parcel_labels()
        if duplicate_parcels:
            errors.append(
                _("Each parcel can only be added to a box once. Duplicate parcel(s): %s")
                % ", ".join(duplicate_parcels)
            )
        if self.collection_point_id and collection_points != self.collection_point_id:
            errors.append(_("All parcels must match the selected box collection point."))
        if len(collection_points) > 1:
            errors.append(_("All parcels in a box must have the same collection point."))
        return errors

    def _get_duplicate_parcel_labels(self):
        self.ensure_one()
        seen = set()
        duplicates = []
        for line in self.line_ids:
            parcel = line.bagging_qa_id
            if not parcel:
                continue
            if parcel.id in seen:
                duplicates.append(parcel.parcel_reference or parcel.display_name)
            seen.add(parcel.id)
        return duplicates

    def _validate_unique_parcels(self):
        for box in self:
            duplicate_parcels = box._get_duplicate_parcel_labels()
            if duplicate_parcels:
                raise ValidationError(
                    _("Each parcel can only be added to a box once. Duplicate parcel(s): %s")
                    % ", ".join(duplicate_parcels)
                )

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
                box.with_context(skip_box_rule_change_guard=True).write(values)

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

    def _get_scan_parcel_id(self, parcel_value):
        if isinstance(parcel_value, (list, tuple)):
            parcel_value = parcel_value[0] if parcel_value else False
        if isinstance(parcel_value, dict):
            parcel_value = (
                parcel_value.get("resId")
                or parcel_value.get("id")
                or (parcel_value.get("data") or {}).get("resId")
                or (parcel_value.get("data") or {}).get("id")
            )
        if isinstance(parcel_value, (list, tuple)):
            parcel_value = parcel_value[0] if parcel_value else False
        if isinstance(parcel_value, str):
            parcel_value = int(parcel_value) if parcel_value.isdigit() else False
        return parcel_value if isinstance(parcel_value, int) else False

    @api.model
    def action_scan_parcel_in_new_box(self, parcel_id, max_parcels=False):
        self._ensure_boxing_access()
        values = {}
        if isinstance(max_parcels, str):
            max_parcels = int(max_parcels) if max_parcels.isdigit() else False
        if isinstance(max_parcels, int) and max_parcels > 0:
            values["max_parcels"] = max_parcels
        box = self.create(values)
        return box.action_scan_parcel(parcel_id)

    def action_scan_parcel(self, parcel_id):
        self._ensure_boxing_access()
        self.ensure_one()
        parcel_id = self._get_scan_parcel_id(parcel_id)
        if not parcel_id:
            raise UserError(_("Select or scan a parcel first."))

        BoxLine = self.env["cdu.box.line"]
        parcel = self.env["cdu.bagging.qa"].browse(parcel_id).exists()
        if (
            not parcel
            or parcel.state != "confirmed"
            or parcel.prescription_id.state != "awaiting_boxing"
        ):
            raise UserError(
                _("Only confirmed parcels awaiting boxing can be added to a box.")
            )

        existing_line = BoxLine.search([("bagging_qa_id", "=", parcel.id)], limit=1)
        if existing_line:
            raise UserError(
                _("%(parcel)s has already been added to box %(box)s.")
                % {
                    "parcel": parcel.parcel_reference or parcel.display_name,
                    "box": existing_line.box_id.name,
                }
            )

        current_box = self
        closed_box = self.env["cdu.box"]
        if current_box.line_ids and (
            current_box.parcel_count >= current_box.max_parcels
            or current_box.collection_point_id != parcel.collection_point_id
        ):
            confirm_action = current_box.action_confirm_box()
            if confirm_action.get("type") != "ir.actions.client":
                return confirm_action
            closed_box = current_box
            current_box = self.create(
                {
                    "collection_point_id": parcel.collection_point_id.id,
                    "next_drug_pickup_date": parcel.next_drug_pickup_date,
                    "max_parcels": closed_box.max_parcels,
                }
            )
        elif not current_box.line_ids:
            current_box.with_context(skip_box_rule_change_guard=True).write(
                {
                    "collection_point_id": parcel.collection_point_id.id,
                    "next_drug_pickup_date": parcel.next_drug_pickup_date,
                }
            )

        BoxLine.create(
            {
                "box_id": current_box.id,
                "bagging_qa_id": parcel.id,
            }
        )

        if closed_box:
            title = _("Box closed, new box opened")
            message = _(
                "%(closed_box)s was closed. %(new_box)s was opened for %(location)s, "
                "and parcel %(parcel)s was added."
            ) % {
                "closed_box": closed_box.name,
                "new_box": current_box.name,
                "location": parcel.collection_point_id.display_name,
                "parcel": parcel.parcel_reference,
            }
            notification_type = "warning"
        else:
            title = _("Parcel added")
            message = _(
                "%(parcel)s was added to %(box)s for %(location)s."
            ) % {
                "parcel": parcel.parcel_reference,
                "box": current_box.name,
                "location": parcel.collection_point_id.display_name,
            }
            notification_type = "success"

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": title,
                "message": message,
                "type": notification_type,
                "sticky": bool(closed_box),
                "next": {
                    "type": "ir.actions.act_window",
                    "name": _("Box"),
                    "res_model": "cdu.box",
                    "res_id": current_box.id,
                    "views": [(False, "form")],
                    "view_mode": "form",
                    "target": "current",
                },
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
    def cron_poll_collect_go_message_status(self):
        return self.env[
            "cdu.collect.go.service"
        ].cron_poll_message_processing_status()

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
                raise UserError(_("%s: dispensed packs must be greater than zero.") % label)

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
            pack_size = (
                line.selected_pack_size
                or line.stock_option_id.pack_size
                or 30
            )
            grouped[key]["quantity"] += line.quantity_dispensed * pack_size
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
            "unique_box_bagging_qa_line",
            "unique(box_id, bagging_qa_id)",
            "This parcel has already been added to this box.",
        ),
    ]

    def init(self):
        self.env.cr.execute(
            """
            ALTER TABLE cdu_box_line
            DROP CONSTRAINT IF EXISTS cdu_box_line_unique_bagging_qa_box_line
            """
        )
        self.env.cr.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                      FROM pg_constraint
                     WHERE conname = 'cdu_box_line_unique_box_bagging_qa_line'
                ) THEN
                    ALTER TABLE cdu_box_line
                    ADD CONSTRAINT cdu_box_line_unique_box_bagging_qa_line
                    UNIQUE (box_id, bagging_qa_id);
                END IF;
            END $$;
            """
        )

    @api.model_create_multi
    def create(self, vals_list):
        box_ids = [vals.get("box_id") for vals in vals_list if vals.get("box_id")]
        if self.env["cdu.box"].browse(box_ids).filtered(lambda box: box.state == "confirmed"):
            raise UserError(_("Parcels cannot be added to a confirmed box."))
        self._validate_create_values_unique_parcels(vals_list)
        records = super().create(vals_list)
        records.mapped("box_id")._sync_from_lines()
        records.mapped("box_id")._validate_unique_parcels()
        records._validate_box_rule_matches()
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
        if "box_id" in vals or "bagging_qa_id" in vals:
            self._validate_write_values_unique_parcels(vals)
        result = super().write(vals)
        self.mapped("box_id")._sync_from_lines()
        self.mapped("box_id")._validate_unique_parcels()
        self._validate_box_rule_matches()
        return result

    @api.model
    def _validate_create_values_unique_parcels(self, vals_list):
        parcels_by_box = {}
        for vals in vals_list:
            box_id = vals.get("box_id")
            parcel_id = vals.get("bagging_qa_id")
            if not (box_id and parcel_id):
                continue
            parcels_by_box.setdefault(box_id, []).append(parcel_id)

        for box_id, parcel_ids in parcels_by_box.items():
            if len(parcel_ids) != len(set(parcel_ids)):
                self._raise_duplicate_parcel_error(parcel_ids[0])

        if not parcels_by_box:
            return

        domains = []
        for box_id, parcel_ids in parcels_by_box.items():
            domains.append(
                [
                    ("box_id", "=", box_id),
                    ("bagging_qa_id", "in", list(set(parcel_ids))),
                ]
            )
        domain = expression.OR(domains)
        duplicate = self.search(domain, limit=1)
        if duplicate:
            self._raise_duplicate_parcel_error(duplicate.bagging_qa_id.id)

    def _validate_write_values_unique_parcels(self, vals):
        for line in self:
            box_id = vals.get("box_id", line.box_id.id)
            parcel_id = vals.get("bagging_qa_id", line.bagging_qa_id.id)
            if not (box_id and parcel_id):
                continue
            duplicate = self.search(
                [
                    ("id", "!=", line.id),
                    ("box_id", "=", box_id),
                    ("bagging_qa_id", "=", parcel_id),
                ],
                limit=1,
            )
            if duplicate:
                self._raise_duplicate_parcel_error(parcel_id)

    def _raise_duplicate_parcel_error(self, parcel_id):
        parcel = self.env["cdu.bagging.qa"].browse(parcel_id)
        parcel_label = parcel.parcel_reference or parcel.display_name
        raise ValidationError(_("%s has already been added to this box.") % parcel_label)

    def unlink(self):
        boxes = self.mapped("box_id")
        if boxes.filtered(lambda box: box.state == "confirmed"):
            raise UserError(_("Parcels cannot be removed from a confirmed box."))
        result = super().unlink()
        boxes._sync_from_lines()
        return result

    def _validate_box_rule_matches(self):
        for line in self:
            box = line.box_id
            parcel = line.bagging_qa_id
            if not box or not parcel:
                continue
            if parcel.state != "confirmed" or parcel.prescription_id.state != "awaiting_boxing":
                raise UserError(
                    _("Only confirmed parcels awaiting boxing can be added to a box.")
                )
            if box.collection_point_id and parcel.collection_point_id != box.collection_point_id:
                raise UserError(
                    _("Only parcels for the selected collection point can be added to this box.")
                )
