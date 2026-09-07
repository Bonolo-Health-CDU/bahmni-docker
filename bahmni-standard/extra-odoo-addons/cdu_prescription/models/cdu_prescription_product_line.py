from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class CduPrescriptionProductLine(models.Model):
    _name = "cdu.prescription.product.line"
    _description = "CDU Prescription Product"
    _order = "prescription_id, sequence, id"

    _EDITABLE_PRESCRIPTION_STATES = frozenset(
        (
            "awaiting_verification",
            "awaiting_validation",
            "rejected_to_call_center",
            "rejected_to_facility",
        )
    )

    prescription_id = fields.Many2one(
        "cdu.prescription",
        required=True,
        ondelete="cascade",
        index=True,
    )
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one(
        "product.product",
        string="Product",
        domain=[("product_tmpl_id.cdu_is_drug", "=", True)],
        ondelete="restrict",
    )
    imported_product_name = fields.Char(
        string="Imported Product / Regimen",
        readonly=True,
        help="Original product or regimen text received from the source prescription.",
    )
    dosage_instructions = fields.Text(string="Dosage / Instructions")
    source = fields.Selection(
        [
            ("legacy", "Imported prescription"),
            ("regimen", "Mapped regimen"),
            ("manual", "Manually added"),
            ("backorder", "Backorder"),
        ],
        default="manual",
        required=True,
        readonly=True,
    )
    backorder_required_packs = fields.Float(
        string="Outstanding Packs",
        readonly=True,
        copy=False,
    )
    backorder_required_units = fields.Float(
        string="Outstanding Units",
        readonly=True,
        copy=False,
    )
    backorder_required_days = fields.Float(
        string="Outstanding Days",
        readonly=True,
        copy=False,
    )
    backorder_pack_size = fields.Float(
        string="Backorder Pack Size",
        readonly=True,
        copy=False,
    )

    def init(self):
        """Preserve the former single product/dosage fields as one child row."""
        params = self.env["ir.config_parameter"].sudo()
        migration_key = "cdu_prescription.product_lines_migrated_v1"
        if params.get_param(migration_key):
            return
        self.env.cr.execute(
            """
            INSERT INTO cdu_prescription_product_line (
                prescription_id,
                sequence,
                imported_product_name,
                dosage_instructions,
                source
            )
            SELECT prescription.id,
                   10,
                   CASE
                       WHEN POSITION('=' IN COALESCE(prescription.regimen_prescribed_raw, '')) > 0
                       THEN NULLIF(TRIM(SPLIT_PART(prescription.regimen_prescribed_raw, '=', 2)), '')
                       ELSE NULLIF(TRIM(prescription.regimen_prescribed_raw), '')
                   END,
                   prescription.dosage_instructions,
                   'legacy'
              FROM cdu_prescription prescription
             WHERE NOT EXISTS (
                       SELECT 1
                         FROM cdu_prescription_product_line product_line
                        WHERE product_line.prescription_id = prescription.id
                   )
               AND (
                   COALESCE(TRIM(prescription.regimen_prescribed_raw), '') != ''
                   OR COALESCE(TRIM(prescription.dosage_instructions), '') != ''
               )
            """
        )
        params.set_param(migration_key, "1")

    @api.constrains("product_id", "imported_product_name")
    def _check_product_is_present(self):
        for line in self:
            if not line.product_id and not (line.imported_product_name or "").strip():
                raise ValidationError(_("Select a product before saving the row."))

    @api.constrains("prescription_id", "product_id")
    def _check_duplicate_product(self):
        for line in self.filtered("product_id"):
            duplicate_count = self.search_count(
                [
                    ("prescription_id", "=", line.prescription_id.id),
                    ("product_id", "=", line.product_id.id),
                ]
            )
            if duplicate_count > 1:
                raise ValidationError(
                    _("The same product cannot appear twice on one prescription.")
                )

    @api.model_create_multi
    def create(self, vals_list):
        self._check_target_prescriptions_are_editable(vals_list)
        return super().create(vals_list)

    def write(self, vals):
        self._check_prescriptions_are_editable()
        return super().write(vals)

    def unlink(self):
        self._check_prescriptions_are_editable()
        return super().unlink()

    @api.model
    def _check_target_prescriptions_are_editable(self, vals_list):
        if self.env.context.get("cdu_allow_product_line_sync"):
            return
        prescription_ids = {
            values.get("prescription_id")
            for values in vals_list
            if values.get("prescription_id")
        }
        prescriptions = self.env["cdu.prescription"].browse(prescription_ids)
        if prescriptions.filtered(
            lambda prescription: prescription.state
            not in self._EDITABLE_PRESCRIPTION_STATES
        ):
            raise ValidationError(
                _("Regimen products cannot be added at this prescription stage.")
            )

    def _check_prescriptions_are_editable(self):
        if self.env.context.get("cdu_allow_product_line_sync"):
            return
        if self.filtered(
            lambda line: line.prescription_id.state
            not in self._EDITABLE_PRESCRIPTION_STATES
        ):
            raise ValidationError(
                _("Regimen products cannot be changed at this prescription stage.")
            )
