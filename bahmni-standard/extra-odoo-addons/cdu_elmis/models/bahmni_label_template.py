from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class _TemplateValues(dict):
    def __missing__(self, key):
        return ""


class BahmniLabelTemplate(models.Model):
    _name = "bahmni.label.template"
    _description = "Bahmni Print Label Template"
    _order = "sequence, label_type, id"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    label_type = fields.Char(required=True, index=True)
    printer_key = fields.Char(
        required=True,
        help="Logical printer key resolved by the local Print Agent.",
    )
    width_mm = fields.Float(default=102.0)
    height_mm = fields.Float(default=152.0)
    gap_mm = fields.Float(default=3.0)
    command_language = fields.Selection(
        [
            ("tspl", "TSPL2"),
            ("zpl", "ZPL II"),
            ("pdf", "PDF"),
        ],
        required=True,
        default="pdf",
        index=True,
    )
    template_body = fields.Text(
        help="TSPL/ZPL body. Placeholders use Python format syntax, e.g. {patient_name}.",
    )
    report_name = fields.Char(
        help="QWeb report name used when command language is PDF.",
    )
    active = fields.Boolean(default=True, index=True)

    @api.constrains("command_language", "template_body", "report_name")
    def _check_rendering_source(self):
        for template in self:
            if template.command_language in ("tspl", "zpl") and not (
                template.template_body or ""
            ).strip():
                raise ValidationError(
                    _("TSPL/ZPL print templates require a command template body.")
                )
            if template.command_language == "pdf" and not (
                template.report_name or ""
            ).strip():
                raise ValidationError(_("PDF print templates require a report name."))

    @api.model
    def _get_active_template(self, label_type):
        template = self.search(
            [("label_type", "=", label_type), ("active", "=", True)],
            order="sequence, id",
            limit=1,
        )
        if not template:
            raise UserError(
                _("No active print template is configured for '%s'.") % label_type
            )
        return template

    def render_command_payload(self, values):
        self.ensure_one()
        if self.command_language == "pdf":
            raise UserError(_("PDF templates are rendered by the report engine."))
        render_values = _TemplateValues(
            {
                "width_mm": self._format_template_value(self.width_mm),
                "height_mm": self._format_template_value(self.height_mm),
                "gap_mm": self._format_template_value(self.gap_mm),
            }
        )
        render_values.update(
            {
                key: self._format_template_value(value)
                for key, value in (values or {}).items()
            }
        )
        return (self.template_body or "").format_map(render_values)

    def _format_template_value(self, value):
        if value is False or value is None:
            return ""
        if isinstance(value, float):
            return ("%0.2f" % value).rstrip("0").rstrip(".")
        if isinstance(value, int):
            return str(value)
        value = str(value)
        if self.command_language in ("tspl", "zpl"):
            value = " ".join(value.split())
            value = value.replace('"', "'")
            if self.command_language == "zpl":
                value = value.replace("^", " ").replace("~", " ")
        return value
