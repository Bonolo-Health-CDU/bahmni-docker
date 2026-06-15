from odoo import _, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    cdu_elmis_base_url = fields.Char(
        string="eLMIS Base URL",
        config_parameter="cdu.elmis.base_url",
    )
    cdu_elmis_api_key = fields.Char(
        string="eLMIS API Key",
        config_parameter="cdu.elmis.api_key",
        groups="cdu_prescription.group_cdu_admin",
    )
    cdu_elmis_user_client_id = fields.Char(
        string="eLMIS User OAuth Client ID",
        config_parameter="cdu.elmis.user_client_id",
        default="user-client",
        groups="cdu_prescription.group_cdu_admin",
    )
    cdu_elmis_user_client_secret = fields.Char(
        string="eLMIS User OAuth Client Secret",
        config_parameter="cdu.elmis.user_client_secret",
        default="changeme",
        groups="cdu_prescription.group_cdu_admin",
    )
    cdu_elmis_cdu_store_facility_code = fields.Char(
        string="CDU Store Facility Code",
        config_parameter="cdu.elmis.cdu_store_facility_code",
    )
    cdu_elmis_cdu_production_floor_facility_code = fields.Char(
        string="CDU Production Floor Facility Code",
        config_parameter="cdu.elmis.cdu_production_floor_facility_code",
    )
    cdu_elmis_default_program_code = fields.Char(
        string="Default Program Code",
        config_parameter="cdu.elmis.default_program_code",
    )
    cdu_elmis_cdu_store_facility_id = fields.Char(
        string="CDU Store Facility UUID",
        config_parameter="cdu.elmis.cdu_store_facility_id",
        readonly=True,
    )
    cdu_elmis_cdu_production_floor_facility_id = fields.Char(
        string="CDU Production Floor Facility UUID",
        config_parameter="cdu.elmis.cdu_production_floor_facility_id",
        readonly=True,
    )
    cdu_elmis_default_program_id = fields.Char(
        string="Default Program UUID",
        config_parameter="cdu.elmis.default_program_id",
        readonly=True,
    )
    cdu_elmis_picking_debit_reason_name = fields.Char(
        string="Picking Store Debit Reason",
        config_parameter="cdu.elmis.picking_debit_reason_name",
        default="Transfer Out",
    )
    cdu_elmis_picking_credit_reason_name = fields.Char(
        string="Picking Production Credit Reason",
        config_parameter="cdu.elmis.picking_credit_reason_name",
        default="Transfer In",
    )
    cdu_elmis_consumption_reason_name = fields.Char(
        string="Consumption Reason",
        config_parameter="cdu.elmis.consumption_reason_name",
        default="Consumed",
    )
    cdu_elmis_residual_debit_reason_name = fields.Char(
        string="Residual Production Debit Reason",
        config_parameter="cdu.elmis.residual_debit_reason_name",
        default="Transfer Out",
    )
    cdu_elmis_residual_credit_reason_name = fields.Char(
        string="Residual Store Credit Reason",
        config_parameter="cdu.elmis.residual_credit_reason_name",
        default="Facility Return",
    )
    cdu_elmis_picking_debit_reason_id = fields.Char(
        string="Picking Store Debit Reason UUID",
        config_parameter="cdu.elmis.picking_debit_reason_id",
        readonly=True,
    )
    cdu_elmis_picking_credit_reason_id = fields.Char(
        string="Picking Production Credit Reason UUID",
        config_parameter="cdu.elmis.picking_credit_reason_id",
        readonly=True,
    )
    cdu_elmis_consumption_reason_id = fields.Char(
        string="Consumption Reason UUID",
        config_parameter="cdu.elmis.consumption_reason_id",
        readonly=True,
    )
    cdu_elmis_residual_debit_reason_id = fields.Char(
        string="Residual Production Debit Reason UUID",
        config_parameter="cdu.elmis.residual_debit_reason_id",
        readonly=True,
    )
    cdu_elmis_residual_credit_reason_id = fields.Char(
        string="Residual Store Credit Reason UUID",
        config_parameter="cdu.elmis.residual_credit_reason_id",
        readonly=True,
    )
    cdu_elmis_store_to_production_destination_node_id = fields.Char(
        string="Store to Production Destination Node UUID",
        config_parameter="cdu.elmis.store_to_production_destination_node_id",
        readonly=True,
    )
    cdu_elmis_production_from_store_source_node_id = fields.Char(
        string="Production from Store Source Node UUID",
        config_parameter="cdu.elmis.production_from_store_source_node_id",
        readonly=True,
    )
    cdu_elmis_production_to_store_destination_node_id = fields.Char(
        string="Production to Store Destination Node UUID",
        config_parameter="cdu.elmis.production_to_store_destination_node_id",
        readonly=True,
    )
    cdu_elmis_store_from_production_source_node_id = fields.Char(
        string="Store from Production Source Node UUID",
        config_parameter="cdu.elmis.store_from_production_source_node_id",
        readonly=True,
    )
    cdu_elmis_stock_cache_ttl_seconds = fields.Integer(
        string="Stock Cache TTL (Seconds)",
        config_parameter="cdu.elmis.stock_cache_ttl_seconds",
        default=300,
    )
    cdu_elmis_max_retry_count = fields.Integer(
        string="Max Retry Count",
        config_parameter="cdu.elmis.max_retry_count",
        default=3,
    )
    cdu_elmis_mapping_service_backend = fields.Char(
        string="Mapping Service Backend",
        config_parameter="cdu.elmis.mapping_service_backend",
        default="local",
    )
    cdu_collect_go_base_url = fields.Char(
        string="Collect-and-Go Base URL",
        config_parameter="cdu.collect_go.base_url",
        default="https://api.bonolohealth.gov.ls/api/Ministry%20of%20Health",
    )
    cdu_collect_go_cdu_location_id = fields.Char(
        string="Collect-and-Go CDU Location ID",
        config_parameter="cdu.collect_go.cdu_location_id",
    )
    cdu_collect_go_dispatch_tracking_status_type = fields.Char(
        string="Dispatch Tracking Status Type",
        config_parameter="cdu.collect_go.dispatch_tracking_status_type",
        default="ReadyForDispatch",
    )
    cdu_collect_go_standard_parcel_type = fields.Integer(
        string="Standard Parcel Type",
        config_parameter="cdu.collect_go.standard_parcel_type",
        default=0,
    )
    cdu_collect_go_box_parcel_type = fields.Integer(
        string="Box Parcel Type",
        config_parameter="cdu.collect_go.box_parcel_type",
        default=2,
    )
    cdu_collect_go_poll_attempts = fields.Integer(
        string="Poll Attempts",
        config_parameter="cdu.collect_go.poll_attempts",
        default=10,
    )
    cdu_collect_go_poll_interval_seconds = fields.Integer(
        string="Poll Interval (Seconds)",
        config_parameter="cdu.collect_go.poll_interval_seconds",
        default=3,
    )
    cdu_collect_go_timeout_seconds = fields.Integer(
        string="HTTP Timeout (Seconds)",
        config_parameter="cdu.collect_go.timeout_seconds",
        default=30,
    )
    cdu_collect_go_max_retry_count = fields.Integer(
        string="Collect-and-Go Max Retry Count",
        config_parameter="cdu.collect_go.max_retry_count",
        default=3,
    )
    cdu_collect_go_message_poll_enabled = fields.Boolean(
        string="Enable Message Processing Polling",
        config_parameter="cdu.collect_go.message_poll_enabled",
        default=True,
    )
    cdu_collect_go_message_poll_interval_minutes = fields.Integer(
        string="Message Poll Interval (Minutes)",
        config_parameter="cdu.collect_go.message_poll_interval_minutes",
        default=1,
    )
    cdu_collect_go_message_poll_batch_size = fields.Integer(
        string="Message Poll Batch Size",
        config_parameter="cdu.collect_go.message_poll_batch_size",
        default=100,
    )
    cdu_collect_go_status_poll_enabled = fields.Boolean(
        string="Enable Parcel Status Polling",
        config_parameter="cdu.collect_go.status_poll_enabled",
        default=True,
    )
    cdu_collect_go_status_poll_interval_minutes = fields.Integer(
        string="Parcel Status Poll Interval (Minutes)",
        config_parameter="cdu.collect_go.status_poll_interval_minutes",
        default=15,
    )

    def action_test_collect_go_connection(self):
        self.ensure_one()
        self.execute()
        return self.env["cdu.collect.go.service"].action_test_connection()

    def set_values(self):
        result = super().set_values()
        self._sync_collect_go_poll_crons()
        return result

    def _sync_collect_go_poll_crons(self):
        params = self.env["ir.config_parameter"].sudo()
        self._sync_collect_go_poll_cron(
            "cdu_elmis.ir_cron_cdu_collect_go_message_poll",
            params.get_param(
                "cdu.collect_go.message_poll_enabled", "True"
            )
            == "True",
            params.get_param(
                "cdu.collect_go.message_poll_interval_minutes"
            ),
            1,
        )
        self._sync_collect_go_poll_cron(
            "cdu_elmis.ir_cron_cdu_collect_go_status_poll",
            params.get_param(
                "cdu.collect_go.status_poll_enabled", "True"
            )
            == "True",
            params.get_param(
                "cdu.collect_go.status_poll_interval_minutes"
            ),
            15,
        )

    def _sync_collect_go_poll_cron(
        self,
        xml_id,
        enabled,
        raw_interval,
        default_interval,
    ):
        cron = self.env.ref(xml_id, raise_if_not_found=False)
        if not cron:
            return
        try:
            interval = int(raw_interval or default_interval)
        except ValueError:
            interval = default_interval
        cron.sudo().write(
            {
                "active": enabled,
                "interval_number": max(interval, 1),
            }
        )

    def action_resolve_elmis_reference_ids(self):
        self.ensure_one()
        self.execute()
        self.env["cdu.elmis.stock.service"].resolve_configured_reference_ids()
        self._load_resolved_elmis_reference_ids()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("eLMIS UUIDs resolved"),
                "message": _(
                    "Facility, program, reason, and transfer node UUIDs were populated."
                ),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    def _load_resolved_elmis_reference_ids(self):
        params = self.env["ir.config_parameter"].sudo()
        field_param_pairs = {
            "cdu_elmis_cdu_store_facility_id": "cdu.elmis.cdu_store_facility_id",
            "cdu_elmis_cdu_production_floor_facility_id": "cdu.elmis.cdu_production_floor_facility_id",
            "cdu_elmis_default_program_id": "cdu.elmis.default_program_id",
            "cdu_elmis_picking_debit_reason_id": "cdu.elmis.picking_debit_reason_id",
            "cdu_elmis_picking_credit_reason_id": "cdu.elmis.picking_credit_reason_id",
            "cdu_elmis_consumption_reason_id": "cdu.elmis.consumption_reason_id",
            "cdu_elmis_residual_debit_reason_id": "cdu.elmis.residual_debit_reason_id",
            "cdu_elmis_residual_credit_reason_id": "cdu.elmis.residual_credit_reason_id",
            "cdu_elmis_store_to_production_destination_node_id": "cdu.elmis.store_to_production_destination_node_id",
            "cdu_elmis_production_from_store_source_node_id": "cdu.elmis.production_from_store_source_node_id",
            "cdu_elmis_production_to_store_destination_node_id": "cdu.elmis.production_to_store_destination_node_id",
            "cdu_elmis_store_from_production_source_node_id": "cdu.elmis.store_from_production_source_node_id",
        }
        self.write(
            {
                field_name: params.get_param(param_name)
                for field_name, param_name in field_param_pairs.items()
            }
        )
