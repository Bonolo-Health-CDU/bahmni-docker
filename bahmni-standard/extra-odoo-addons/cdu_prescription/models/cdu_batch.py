from odoo import api, fields, models

class CduBatch(models.Model):
    _name = "cdu.batch"
    _description = "CDU Workload Batch"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc"

    name = fields.Char(string="Batch Reference", required=True, copy=False, readonly=True, default="New")
    state = fields.Selection([
        ('draft', 'Draft (Filtering)'),
        ('confirmed', 'Batch Confirmed'),
        ('printed', 'Picking List Printed'),
        ('done', 'Done')
    ], default='draft', tracking=True)

    # --- Primary Filters ---
    filter_refill_date = fields.Date(string="Refill Date")
    filter_repeat_count = fields.Integer(string="Number of Repeats")
    filter_diagnosis = fields.Selection([
        ('asthma_copd', 'Asthma/COPD'),
        ('diabetes_type_2', 'Diabetes Mellitus - Type 2'),
        ('family_planning', 'Family Planning'),
        ('hypertension', 'Hypertension'),
        ('hiv', 'HIV'),
        ('arthritis', 'Arthritis'),
    ], string="Diagnosis (Program)")

    # --- Secondary Filters ---
    filter_collection_point_id = fields.Many2one('cdu.collection.point', string="Pick-up Location")
    filter_facility_id = fields.Many2one('cdu.facility', string="Clinic / Facility")

    # --- Results ---
    prescription_ids = fields.One2many(
        'cdu.prescription', 
        'batch_id', 
        string="Prescriptions in Batch"
    )

    @api.model
    def create(self, vals):
        if vals.get('name', 'New') == 'New':
            vals['name'] = self.env['ir.sequence'].next_by_code('cdu.batch') or 'New'
        return super().create(vals)

    @api.onchange('filter_refill_date', 'filter_repeat_count', 'filter_diagnosis', 'filter_collection_point_id', 'filter_facility_id')
    def _onchange_filters_fetch_prescriptions(self):
        """
        Dynamically fetches un-batched prescriptions based on applied filters.
        """
        if self.state != 'draft':
            return

        # Start with base domain: must be awaiting batching and not already in a batch
        domain = [('state', '=', 'awaiting_batching'), ('batch_id', '=', False)]
        
        # Apply standard filters
        if self.filter_refill_date:
            domain.append(('refill_date', '=', self.filter_refill_date))
        if self.filter_repeat_count:
            domain.append(('repeat_count', '=', self.filter_repeat_count))
        if self.filter_collection_point_id:
            domain.append(('collection_point_id', '=', self.filter_collection_point_id.id))
        if self.filter_facility_id:
            domain.append(('facility_id', '=', self.filter_facility_id.id))

        # Apply Diagnosis boolean mapping[cite: 11]
        if self.filter_diagnosis:
            diagnosis_field_map = {
                'asthma_copd': 'diagnosis_asthma_copd',
                'diabetes_type_2': 'diagnosis_diabetes_type_2',
                'family_planning': 'diagnosis_family_planning',
                'hypertension': 'diagnosis_hypertension',
                'hiv': 'diagnosis_hiv',
                'arthritis': 'diagnosis_arthritis',
            }
            mapped_field = diagnosis_field_map.get(self.filter_diagnosis)
            if mapped_field:
                domain.append((mapped_field, '=', True))

        # Fetch and link records
        prescriptions = self.env['cdu.prescription'].search(domain)
        self.prescription_ids = [(6, 0, prescriptions.ids)]

    def action_confirm_batch(self):
        """Confirming the batch locks the prescriptions to it."""
        for record in self:
            record.write({'state': 'confirmed'})