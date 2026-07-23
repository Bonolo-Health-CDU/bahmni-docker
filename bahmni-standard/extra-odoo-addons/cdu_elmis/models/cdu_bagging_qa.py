from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class CduBaggingQa(models.Model):
    _name = "cdu.bagging.qa"
    _description = "CDU Bagging and QA"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc"

    name = fields.Char(default="/", copy=False, readonly=True, tracking=True)
    parcel_reference = fields.Char(copy=False, readonly=True, tracking=True, index=True)
    prescription_id = fields.Many2one(
        "cdu.prescription",
        required=True,
        ondelete="restrict",
        index=True,
        tracking=True,
    )
    dispense_id = fields.Many2one(
        "cdu.dispense",
        required=True,
        ondelete="restrict",
        index=True,
        tracking=True,
    )
    batch_id = fields.Many2one(
        "cdu.batch",
        related="prescription_id.batch_id",
        store=True,
        readonly=True,
    )
    patient_id = fields.Many2one(
        "res.partner",
        related="prescription_id.patient_id",
        store=True,
        readonly=True,
    )
    patient_name = fields.Char(
        related="prescription_id.patient_first_name",
        store=True,
        readonly=True,
    )
    patient_phone = fields.Char(
        related="prescription_id.patient_phone",
        store=True,
        readonly=True,
    )
    regimen_prescribed_raw = fields.Char(
        related="prescription_id.regimen_prescribed_raw",
        store=True,
        readonly=True,
    )
    collection_point_id = fields.Many2one(
        "cdu.collection.point",
        related="prescription_id.collection_point_id",
        store=True,
        readonly=True,
    )
    next_drug_pickup_date = fields.Date(
        related="prescription_id.next_drug_pickup_date",
        store=True,
        readonly=True,
    )
    labels_printed = fields.Boolean(
        related="dispense_id.labels_printed",
        store=True,
        readonly=True,
    )
    labels_printed_at = fields.Datetime(
        related="dispense_id.labels_printed_at",
        store=True,
        readonly=True,
    )
    stock_selection_ids = fields.One2many(
        "cdu.dispense.stock.selection",
        related="dispense_id.stock_selection_ids",
        readonly=True,
        string="Dispensed Products",
    )
    patient_details_checked = fields.Boolean(
        string="Patient details checked",
        readonly=True,
        tracking=True,
    )
    medicine_product_checked = fields.Boolean(
        string="Medicine/product checked",
        readonly=True,
        tracking=True,
    )
    quantity_checked = fields.Boolean(
        string="Quantity checked",
        readonly=True,
        tracking=True,
    )
    dosing_instructions_checked = fields.Boolean(
        string="Dosing instructions checked",
        readonly=True,
        tracking=True,
    )
    product_labels_attached = fields.Boolean(
        string="Product labels attached",
        readonly=True,
        tracking=True,
    )
    bag_label_attached = fields.Boolean(
        string="Bag label attached",
        readonly=True,
        tracking=True,
    )
    medicines_placed_in_bag = fields.Boolean(
        string="Medicines placed in bag",
        readonly=True,
        tracking=True,
    )
    labels_attached = fields.Boolean(
        string="Labels attached",
        readonly=True,
        tracking=True,
    )
    bag_sealed = fields.Boolean(
        string="Bag sealed",
        readonly=True,
        tracking=True,
    )
    qa_notes = fields.Text(string="QA Notes", tracking=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("confirmed", "Confirmed"),
        ],
        default="draft",
        tracking=True,
    )
    qa_ready = fields.Boolean(compute="_compute_qa_readiness", store=True)
    qa_readiness_message = fields.Text(compute="_compute_qa_readiness", compute_sudo=True)
    label_qr_scan_input = fields.Text(
        string="Scan Bag Label to Pass QA",
        compute="_compute_label_qr_scan_input",
        inverse="_inverse_label_qr_scan_input",
        store=False,
        help=(
            "Scan the bag label QR after checking the patient, medicines, quantity, "
            "dosing instructions, labels, bag contents, and seal."
        ),
    )
    bagged_by = fields.Many2one("res.users", readonly=True)
    bagged_at = fields.Datetime(readonly=True)
    qa_checked_by = fields.Many2one("res.users", readonly=True)
    qa_checked_at = fields.Datetime(readonly=True)
    confirmed_by = fields.Many2one("res.users", readonly=True)
    confirmed_at = fields.Datetime(readonly=True)

    _sql_constraints = [
        (
            "unique_prescription_bagging_qa",
            "unique(prescription_id)",
            "Only one Bagging / QA record is allowed per prescription.",
        ),
    ]

    @api.model
    def create(self, vals):
        if vals.get("name", "/") == "/":
            vals["name"] = self.env["ir.sequence"].next_by_code("cdu.bagging.qa") or "/"
        if vals.get("prescription_id") and not vals.get("dispense_id"):
            dispense = self.env["cdu.dispense"].search(
                [("prescription_id", "=", vals["prescription_id"])],
                limit=1,
            )
            if dispense:
                vals["dispense_id"] = dispense.id
        return super().create(vals)

    def _ensure_bagging_qa_access(self):
        if not (
            self.env.user.has_group("cdu_prescription.group_cdu_dispensing_officer")
            or self.env.user.has_group("cdu_prescription.group_cdu_admin")
        ):
            raise AccessError(_("Only CDU dispensing officers can manage Bagging / QA."))

    def _compute_label_qr_scan_input(self):
        for qa in self:
            qa.label_qr_scan_input = False

    def _inverse_label_qr_scan_input(self):
        return

    @api.depends(
        "dispense_id.state",
        "dispense_id.labels_printed",
        "dispense_id.stock_selection_ids",
        "patient_details_checked",
        "medicine_product_checked",
        "quantity_checked",
        "dosing_instructions_checked",
        "product_labels_attached",
        "bag_label_attached",
        "medicines_placed_in_bag",
        "bag_sealed",
        "stock_selection_ids",
    )
    def _compute_qa_readiness(self):
        for qa in self:
            errors = qa._get_qa_readiness_errors()
            qa.qa_ready = not errors
            qa.qa_readiness_message = (
                _("Ready for Bagging / QA confirmation.") if not errors else "\n".join(errors)
            )

    def _get_qa_readiness_errors(self):
        self.ensure_one()
        errors = []
        if self.dispense_id.state != "confirmed":
            errors.append(_("Dispensing must be confirmed before Bagging / QA."))
        if not self.labels_printed:
            errors.append(_("Labels must be printed before Bagging / QA can be confirmed."))
        if not self.stock_selection_ids:
            errors.append(_("No dispensed products were recorded for this prescription."))

        checklist = [
            (self.patient_details_checked, _("Patient details must be checked.")),
            (self.medicine_product_checked, _("Medicine/product must be checked.")),
            (self.quantity_checked, _("Quantity must be checked.")),
            (self.dosing_instructions_checked, _("Dosing instructions must be checked.")),
            (self.product_labels_attached, _("Product labels must be attached.")),
            (self.bag_label_attached, _("Bag label must be attached.")),
            (self.medicines_placed_in_bag, _("Medicines must be placed in the bag.")),
            (self.bag_sealed, _("Bag must be sealed.")),
        ]
        errors.extend(message for passed, message in checklist if not passed)
        return errors

    def _ensure_qa_ready(self):
        self.ensure_one()
        errors = self._get_qa_readiness_errors()
        if errors:
            raise ValidationError(_("Bagging / QA cannot be confirmed yet:\n\n%s") % "\n".join(errors))

    def _confirm_bagging_qa_record(self):
        self.ensure_one()
        if self.state == "confirmed":
            raise UserError(_("Bagging / QA has already been confirmed for %s.") % self.name)
        self._ensure_qa_ready()
        now = fields.Datetime.now()
        self.write(
            {
                "parcel_reference": self.parcel_reference
                or self.env["ir.sequence"].next_by_code("cdu.parcel")
                or "/",
                "state": "confirmed",
                "confirmed_by": self.env.user.id,
                "confirmed_at": now,
                "bagged_by": self.bagged_by.id or self.env.user.id,
                "bagged_at": self.bagged_at or now,
                "qa_checked_by": self.qa_checked_by.id or self.env.user.id,
                "qa_checked_at": self.qa_checked_at or now,
            }
        )
        if self.prescription_id.state == "awaiting_bagging_qa":
            self.prescription_id.write({"state": "awaiting_boxing"})

    def _get_next_bagging_qa_action(self):
        self.ensure_one()
        domain = [
            ("state", "=", "awaiting_bagging_qa"),
            ("id", "!=", self.prescription_id.id),
        ]
        if self.batch_id:
            domain.append(("batch_id", "=", self.batch_id.id))
        else:
            domain.append(("batch_id", "=", False))

        next_prescription = self.env["cdu.prescription"].search(
            domain,
            order="next_drug_pickup_date asc, patient_first_name asc, id asc",
            limit=1,
        )
        if next_prescription:
            return next_prescription.action_open_bagging_qa()

        action = self.env.ref("cdu_elmis.action_cdu_bagging_qa_work_queue").read()[0]
        action["views"] = [(False, "tree"), (False, "form")]
        if self.batch_id:
            action["domain"] = [
                ("state", "=", "awaiting_bagging_qa"),
                ("batch_id", "=", self.batch_id.id),
            ]
        return action

    def action_confirm_bagging_qa(self):
        self._ensure_bagging_qa_access()
        for qa in self:
            qa._confirm_bagging_qa_record()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Bagging / QA confirmed"),
                "message": _("Prescription moved to Awaiting Boxing."),
                "type": "success",
                "sticky": False,
                "className": (
                    "o_cdu_scan_notification "
                    "o_cdu_scan_notification_success"
                ),
                "next": self[:1]._get_next_bagging_qa_action() if len(self) == 1 else False,
            },
        }

    def action_print_labels(self):
        self._ensure_bagging_qa_access()
        dispenses = self.mapped("dispense_id")
        if not dispenses:
            raise UserError(_("This Bagging / QA record is not linked to a dispense record."))
        return dispenses.action_print_labels()

    def action_scan_label_qr(self):
        self.ensure_one()
        self._ensure_bagging_qa_access()
        if self.state != "draft":
            raise UserError(_("QR verification is only available for draft Bagging / QA records."))
        return {
            "type": "ir.actions.client",
            "tag": "cdu_bagging_qa_scan_qr",
            "params": {"qa_id": self.id},
        }

    def action_validate_label_qr(self, scanned_value):
        self.ensure_one()
        self._ensure_bagging_qa_access()
        if self.state != "draft":
            raise UserError(_("QR verification is only available for draft Bagging / QA records."))

        values = [
            value.strip()
            for value in str(scanned_value or "").replace("\r\n", "\n").split("\n")
            if value.strip()
        ]
        if len(values) != 2:
            raise UserError(
                _("This is not a valid CDU bag label QR code. Please scan the QR code on the bag.")
            )

        scanned_patient, scanned_prescription = values
        expected_patient = (self.patient_name or "").strip()
        expected_prescription = (self.prescription_id.name or "").strip()
        normalize = lambda value: " ".join(value.split()).casefold()

        if (
            normalize(scanned_patient) != normalize(expected_patient)
            or normalize(scanned_prescription) != normalize(expected_prescription)
        ):
            raise UserError(
                _(
                    "Wrong box scanned.\n\n"
                    "Expected: %(patient)s / %(prescription)s\n"
                    "Scanned: %(scanned_patient)s / %(scanned_prescription)s"
                )
                % {
                    "patient": expected_patient,
                    "prescription": expected_prescription,
                    "scanned_patient": scanned_patient,
                    "scanned_prescription": scanned_prescription,
                }
            )

        self.write(
            {
                "patient_details_checked": True,
                "medicine_product_checked": True,
                "quantity_checked": True,
                "dosing_instructions_checked": True,
                "product_labels_attached": True,
                "bag_label_attached": True,
                "medicines_placed_in_bag": True,
                "bag_sealed": True,
                "qa_checked_by": self.env.user.id,
                "qa_checked_at": fields.Datetime.now(),
            }
        )
        self._confirm_bagging_qa_record()
        next_action = self._get_next_bagging_qa_action()
        next_message = (
            _("QA passed for %(patient)s. Opening the next prescription...")
            if next_action.get("res_model") == "cdu.bagging.qa" and next_action.get("res_id")
            else _("QA passed for %(patient)s. Returning to the QA work queue...")
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Bag passed QA"),
                "message": next_message % {"patient": expected_patient},
                "type": "success",
                "sticky": False,
                "className": (
                    "o_cdu_scan_notification "
                    "o_cdu_scan_notification_success"
                ),
                "next": next_action,
            },
        }

    def _action_open(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Bagging / QA"),
            "res_model": "cdu.bagging.qa",
            "res_id": self.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
        }
