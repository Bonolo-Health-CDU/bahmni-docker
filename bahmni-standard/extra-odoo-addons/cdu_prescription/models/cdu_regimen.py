from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class CduMedicine(models.Model):
    _name = "cdu.medicine"
    _description = "CDU Medicine"
    _order = "name"

    name = fields.Char(required=True, index=True)
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("cdu_medicine_name_unique", "unique(name)", "Medicine names must be unique."),
    ]


class CduRegimen(models.Model):
    _name = "cdu.regimen"
    _description = "CDU Regimen"
    _order = "eregister_name"

    name = fields.Char(string="Description", required=True)
    eregister_name = fields.Char(
        string="Exact eRegister Regimen",
        required=True,
        index=True,
        help="Must match the imported eRegister regimen exactly (apart from outer spaces).",
    )
    active = fields.Boolean(default=True)
    option_ids = fields.One2many(
        "cdu.regimen.option", "regimen_id", string="Dispensing Options"
    )

    _sql_constraints = [
        (
            "cdu_regimen_eregister_name_unique",
            "unique(eregister_name)",
            "The exact eRegister regimen must be unique.",
        ),
    ]

    @api.model
    def find_exact(self, regimen_text):
        regimen_text = (regimen_text or "").strip()
        if not regimen_text:
            return self.browse()
        return self.search(
            [("eregister_name", "=", regimen_text), ("active", "=", True)], limit=1
        )

    def _get_default_option(self):
        self.ensure_one()
        options = self.option_ids.filtered("active").sorted(lambda option: (option.sequence, option.id))
        return options.filtered("is_default")[:1] or options[:1]


class CduRegimenOption(models.Model):
    _name = "cdu.regimen.option"
    _description = "CDU Regimen Dispensing Option"
    _order = "regimen_id, sequence, id"

    name = fields.Char(required=True)
    regimen_id = fields.Many2one(
        "cdu.regimen", required=True, ondelete="cascade", index=True
    )
    sequence = fields.Integer(default=10)
    is_default = fields.Boolean(string="Default Option")
    active = fields.Boolean(default=True)
    line_ids = fields.One2many(
        "cdu.regimen.option.line", "option_id", string="Medicines", copy=True
    )

    @api.constrains("is_default", "regimen_id", "active")
    def _check_single_default(self):
        for option in self.filtered(lambda item: item.is_default and item.active):
            duplicate = self.search_count(
                [
                    ("regimen_id", "=", option.regimen_id.id),
                    ("is_default", "=", True),
                    ("active", "=", True),
                    ("id", "!=", option.id),
                ]
            )
            if duplicate:
                raise ValidationError(_("Only one default option is allowed per regimen."))

    @api.constrains("line_ids")
    def _check_has_medicines(self):
        for option in self.filtered("active"):
            if not option.line_ids:
                raise ValidationError(_("A dispensing option must contain at least one medicine."))


class CduRegimenOptionLine(models.Model):
    _name = "cdu.regimen.option.line"
    _description = "CDU Regimen Option Medicine"
    _order = "option_id, sequence, id"

    option_id = fields.Many2one(
        "cdu.regimen.option", required=True, ondelete="cascade", index=True
    )
    sequence = fields.Integer(default=10)
    medicine_id = fields.Many2one(
        "cdu.medicine", required=True, ondelete="restrict", domain=[("active", "=", True)]
    )

    _sql_constraints = [
        (
            "cdu_regimen_option_medicine_unique",
            "unique(option_id, medicine_id)",
            "A medicine can only appear once in a dispensing option.",
        ),
    ]


class CduPrescriptionMedicineLine(models.Model):
    _name = "cdu.prescription.medicine.line"
    _description = "Prescription Medicine"
    _order = "prescription_id, sequence, id"

    prescription_id = fields.Many2one(
        "cdu.prescription", required=True, ondelete="cascade", index=True
    )
    sequence = fields.Integer(default=10)
    medicine_id = fields.Many2one(
        "cdu.medicine",
        string="Medicine",
        required=True,
        ondelete="restrict",
        domain=[("active", "=", True)],
    )
    dosage_instructions = fields.Text(string="Dosage Instructions")
    source = fields.Selection(
        [("regimen", "From Regimen"), ("manual", "Added Manually")],
        required=True,
        default="manual",
        readonly=True,
    )
    source_option_line_id = fields.Many2one(
        "cdu.regimen.option.line", ondelete="set null", readonly=True
    )
    last_changed_by = fields.Many2one(
        "res.users", readonly=True, default=lambda self: self.env.user
    )
    last_changed_at = fields.Datetime(
        readonly=True, default=fields.Datetime.now
    )

    _sql_constraints = [
        (
            "cdu_prescription_medicine_unique",
            "unique(prescription_id, medicine_id)",
            "A medicine can only appear once on a prescription.",
        ),
    ]

    def _mark_prescriptions_amended(self, invalidate_picking=False, message=None):
        if self.env.context.get("cdu_skip_medicine_audit"):
            return
        prescriptions = self.mapped("prescription_id")
        if not prescriptions:
            return
        prescriptions.with_context(cdu_skip_regimen_sync=True).write(
            {"medicine_amended": True}
        )
        if invalidate_picking:
            prescriptions._invalidate_unconfirmed_picking()
        if message:
            for prescription in prescriptions:
                prescription.message_post(body=Markup("<p>%s</p>") % escape(message))

    @api.model_create_multi
    def create(self, vals_list):
        prescriptions = self.env["cdu.prescription"].browse(
            [values.get("prescription_id") for values in vals_list if values.get("prescription_id")]
        )
        prescriptions._ensure_medicine_composition_editable()
        now = fields.Datetime.now()
        prepared = []
        for values in vals_list:
            values = dict(values)
            values.setdefault("last_changed_by", self.env.user.id)
            values.setdefault("last_changed_at", now)
            prepared.append(values)
        records = super().create(prepared)
        records._mark_prescriptions_amended(
            invalidate_picking=True,
            message=_("A medicine was added to the prescription."),
        )
        return records

    def write(self, vals):
        composition_changed = bool({"medicine_id"}.intersection(vals))
        self.mapped("prescription_id")._ensure_medicine_composition_editable()
        prepared = dict(vals)
        prepared["last_changed_by"] = self.env.user.id
        prepared["last_changed_at"] = fields.Datetime.now()
        result = super().write(prepared)
        clinically_changed = bool(
            {"medicine_id", "dosage_instructions"}.intersection(vals)
        )
        if clinically_changed:
            self._mark_prescriptions_amended(
                invalidate_picking=composition_changed,
                message=_("A prescription medicine or its dosage instructions was changed."),
            )
        return result

    def unlink(self):
        prescriptions = self.mapped("prescription_id")
        prescriptions._ensure_medicine_composition_editable()
        audited = not self.env.context.get("cdu_skip_medicine_audit")
        result = super().unlink()
        if audited:
            prescriptions.with_context(cdu_skip_regimen_sync=True).write(
                {"medicine_amended": True}
            )
            prescriptions._invalidate_unconfirmed_picking()
            for prescription in prescriptions:
                prescription.message_post(
                    body=_("A medicine was removed from the prescription.")
                )
        return result
