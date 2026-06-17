/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { Many2OneField } from "@web/views/fields/many2one/many2one_field";

patch(Many2OneField.prototype, "cdu_elmis.BoxParcelScannerMany2OneField", {
    setup() {
        this._super(...arguments);
        const originalUpdate = this.update;
        this.update = async (value, params = {}) => {
            if (this.shouldUseBoxParcelScanAction(value)) {
                return this.scanBoxParcel(value);
            }
            return originalUpdate.call(this, value, params);
        };
    },

    getBoxParcelId(value) {
        if (Array.isArray(value)) {
            return this.getBoxParcelId(value[0]);
        }
        if (value && typeof value === "object") {
            return (
                this.getBoxParcelId(value.resId) ||
                this.getBoxParcelId(value.id) ||
                this.getBoxParcelId(value.data)
            );
        }
        if (typeof value === "string") {
            return /^\d+$/.test(value) ? Number(value) : false;
        }
        return value;
    },

    getBoxFormRootRecord() {
        return this.props.record && this.props.record.model && this.props.record.model.root;
    },

    getBoxId() {
        const root = this.getBoxFormRootRecord();
        return root && (root.resId || root.data.id);
    },

    getBoxMaxParcels() {
        const root = this.getBoxFormRootRecord();
        return root && root.data.max_parcels;
    },

    shouldUseBoxParcelScanAction(value) {
        return (
            this.props.name === "bagging_qa_id" &&
            this.props.record.resModel === "cdu.box.line" &&
            Boolean(this.getBoxParcelId(value))
        );
    },

    async scanBoxParcel(value) {
        const boxId = this.getBoxId();
        const parcelId = this.getBoxParcelId(value);
        if (!boxId) {
            const action = await this.env.services.orm.call(
                "cdu.box",
                "action_scan_parcel_in_new_box",
                [parcelId, this.getBoxMaxParcels()]
            );
            return this.env.services.action.doAction(action);
        }

        const action = await this.env.services.orm.call(
            "cdu.box",
            "action_scan_parcel",
            [[boxId], parcelId]
        );
        return this.env.services.action.doAction(action);
    },
});
