/** @odoo-module **/

import { browser } from "@web/core/browser/browser";
import { patch } from "@web/core/utils/patch";
import { FormController } from "@web/views/form/form_controller";
import { onMounted, onPatched, onWillUnmount } from "@odoo/owl";

const WIZARD_MODEL = "cdu.elmis.picking.wizard";
const BUTTON_SELECTOR = ".o_cdu_generate_picking_button[name='action_confirm']";
const WARNING_SELECTOR = ".o_cdu_elmis_picking_client_warning";
const EPSILON = 0.0001;

function many2OneId(value) {
    if (Array.isArray(value)) {
        return value[0];
    }
    if (value && typeof value === "object") {
        return value.resId || value.id || value[0];
    }
    return value || false;
}

function many2OneLabel(value) {
    if (Array.isArray(value)) {
        return value[1] || "";
    }
    if (value && typeof value === "object") {
        return value.display_name || value.name || "";
    }
    return "";
}

function numberValue(value) {
    const number = Number(value || 0);
    return Number.isFinite(number) ? number : 0;
}

function formatQty(value) {
    return numberValue(value).toFixed(2);
}

function lineLabel(data, index) {
    return (
        data.openmrs_drug_name ||
        many2OneLabel(data.picking_line_id) ||
        `Fulfilment line ${index + 1}`
    );
}

function validatePickingWizard(record) {
    if (!record || record.resModel !== WIZARD_MODEL) {
        return { valid: true, message: "" };
    }

    const list = record.data.elmis_picking_fulfilment_line_ids;
    const records = (list && list.records) || [];
    if (!records.length) {
        return {
            valid: false,
            message: "Add at least one eLMIS fulfilment line before generating the picking list.",
        };
    }

    const totalsByPickingLine = {};
    const requiredByPickingLine = {};
    const labelsByPickingLine = {};

    for (const [index, lineRecord] of records.entries()) {
        const data = lineRecord.data || {};
        const label = lineLabel(data, index);
        const stockOptionId = many2OneId(data.selected_stock_option_id);
        const pickedQty = numberValue(data.quantity_picked);
        const requiredQty = numberValue(data.required_quantity);
        const stockOnHand = numberValue(data.selected_stock_on_hand);

        if (!stockOptionId) {
            return {
                valid: false,
                message: `${label}: choose an eLMIS stock option before generating the picking list.`,
            };
        }
        if (pickedQty <= 0) {
            return {
                valid: false,
                message: `${label}: picked packs must be greater than zero.`,
            };
        }
        if (requiredQty && pickedQty > requiredQty + EPSILON) {
            return {
                valid: false,
                message: `${label}: picked packs cannot exceed required packs (${formatQty(
                    requiredQty
                )}).`,
            };
        }
        if (stockOnHand && pickedQty > stockOnHand + EPSILON) {
            return {
                valid: false,
                message: `${label}: picked packs (${formatQty(
                    pickedQty
                )}) exceed available packs (${formatQty(stockOnHand)}).`,
            };
        }

        const pickingLineKey = many2OneId(data.picking_line_id) || label;
        totalsByPickingLine[pickingLineKey] = (totalsByPickingLine[pickingLineKey] || 0) + pickedQty;
        requiredByPickingLine[pickingLineKey] = requiredQty;
        labelsByPickingLine[pickingLineKey] = label;
    }

    for (const [pickingLineKey, totalPicked] of Object.entries(totalsByPickingLine)) {
        const requiredQty = requiredByPickingLine[pickingLineKey] || 0;
        if (requiredQty && totalPicked > requiredQty + EPSILON) {
            return {
                valid: false,
                message: `${labelsByPickingLine[pickingLineKey]}: picked packs exceed required packs by ${formatQty(
                    totalPicked - requiredQty
                )}.`,
            };
        }
    }

    return { valid: true, message: "" };
}

patch(FormController.prototype, "cdu_elmis.PickingWizardFormController", {
    setup() {
        this._super(...arguments);
        if (this.props.resModel !== WIZARD_MODEL) {
            return;
        }

        this._cduPickingWizardScheduleUpdate = () => {
            browser.clearTimeout(this._cduPickingWizardUpdateTimer);
            this._cduPickingWizardUpdateTimer = browser.setTimeout(() => {
                this._cduUpdatePickingWizardActionState();
            });
        };

        onMounted(() => {
            document.addEventListener("input", this._cduPickingWizardScheduleUpdate, true);
            document.addEventListener("change", this._cduPickingWizardScheduleUpdate, true);
            document.addEventListener("click", this._cduPickingWizardScheduleUpdate, true);
            this._cduUpdatePickingWizardActionState();
        });
        onPatched(() => this._cduUpdatePickingWizardActionState());
        onWillUnmount(() => {
            browser.clearTimeout(this._cduPickingWizardUpdateTimer);
            document.removeEventListener("input", this._cduPickingWizardScheduleUpdate, true);
            document.removeEventListener("change", this._cduPickingWizardScheduleUpdate, true);
            document.removeEventListener("click", this._cduPickingWizardScheduleUpdate, true);
        });
    },

    async beforeExecuteActionButton(clickParams) {
        if (this.props.resModel === WIZARD_MODEL && clickParams.name === "action_confirm") {
            const saved = await this.model.root.save({
                stayInEdition: true,
                useSaveErrorDialog: true,
            });
            if (saved === false) {
                return false;
            }
            const result = validatePickingWizard(this.model.root);
            this._cduUpdatePickingWizardActionState(result);
            if (!result.valid) {
                this.env.services.notification.add(result.message, {
                    title: "Generate Picking List",
                    type: "warning",
                });
                return false;
            }
            return true;
        }
        return this._super(...arguments);
    },

    _cduUpdatePickingWizardActionState(result = undefined) {
        if (this.props.resModel !== WIZARD_MODEL) {
            return;
        }
        const root = document.querySelector(".o_cdu_elmis_picking_wizard");
        if (!root) {
            return;
        }
        const validation = result || validatePickingWizard(this.model.root);
        const button = root.querySelector(BUTTON_SELECTOR);
        if (button) {
            button.disabled = !validation.valid;
            button.classList.toggle("disabled", !validation.valid);
            button.title = validation.message || "";
        }
        const warning = root.querySelector(WARNING_SELECTOR);
        if (warning) {
            warning.textContent = validation.message || "";
            warning.classList.toggle("d-none", validation.valid);
        }
    },
});
