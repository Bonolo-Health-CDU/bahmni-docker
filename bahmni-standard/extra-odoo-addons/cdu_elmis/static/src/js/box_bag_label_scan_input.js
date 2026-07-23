/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { TextField } from "@web/views/fields/text/text_field";

const SCAN_DEBOUNCE_MS = 500;

export class BoxBagLabelScanInputField extends TextField {
    setup() {
        super.setup();
        this.action = useService("action");
        this.notification = useService("notification");
        this.orm = useService("orm");
        this._isSubmitting = false;
        this._onScanKeydown = this._onScanKeydown.bind(this);
    }

    onMounted() {
        super.onMounted();
        if (this.textareaRef.el && !this.props.readonly) {
            this.textareaRef.el.addEventListener("keydown", this._onScanKeydown);
            browser.setTimeout(() => this._focusScanInput(), 80);
        }
    }

    onWillUnmount() {
        browser.clearTimeout(this._scanTimer);
        if (this.textareaRef.el) {
            this.textareaRef.el.removeEventListener("keydown", this._onScanKeydown);
        }
        super.onWillUnmount();
    }

    onInput() {
        super.onInput(...arguments);
        browser.clearTimeout(this._scanTimer);
        const value = this.textareaRef.el ? this.textareaRef.el.value : "";
        if (String(value || "").trim()) {
            this._scanTimer = browser.setTimeout(
                () => this._submitScan(value),
                SCAN_DEBOUNCE_MS
            );
        }
    }

    _onScanKeydown(ev) {
        if (ev.key === "Escape") {
            ev.preventDefault();
            this._clearAndFocus();
            return;
        }
        if (ev.key === "Enter" && String(ev.currentTarget.value || "").trim()) {
            const value = ev.currentTarget.value;
            ev.preventDefault();
            ev.stopPropagation();
            browser.setTimeout(() => {
                if (!this._isSubmitting) {
                    this._submitScan(value);
                }
            }, 0);
        }
    }

    _focusScanInput() {
        if (this.textareaRef.el && !this.props.readonly) {
            this.textareaRef.el.focus();
            this.textareaRef.el.select();
        }
    }

    async _clearAndFocus() {
        if (this.textareaRef.el) {
            this.textareaRef.el.value = "";
        }
        await this.props.update(false);
        this._focusScanInput();
    }

    async _handleResult(result) {
        if (result && result.type === "ir.actions.client" && result.tag === "display_notification") {
            const params = result.params || {};
            this.notification.add(params.message || _t("Parcel added."), {
                title: params.title || _t("Parcel added"),
                type: params.type || "success",
                sticky: Boolean(params.sticky),
                className:
                    params.className ||
                    "o_cdu_scan_notification o_cdu_scan_notification_success",
            });
            if (params.next) {
                return this.action.doAction(params.next);
            }
            this._focusScanInput();
            return;
        }
        if (result) {
            return this.action.doAction(result);
        }
        this._focusScanInput();
    }

    async _submitScan(value) {
        const scannedValue = String(value || "").trim();
        const boxId = this.props.record && this.props.record.resId;
        if (!scannedValue || this._isSubmitting) {
            return;
        }

        browser.clearTimeout(this._scanTimer);
        this._isSubmitting = true;
        try {
            let result;
            if (boxId) {
                result = await this.orm.call("cdu.box", "action_scan_bag_label", [
                    [boxId],
                    scannedValue,
                ]);
            } else {
                const maxParcels =
                    this.props.record &&
                    this.props.record.data &&
                    this.props.record.data.max_parcels;
                result = await this.orm.call(
                    "cdu.box",
                    "action_scan_bag_label_in_new_box",
                    [scannedValue, maxParcels]
                );
            }
            await this._clearAndFocus();
            await this._handleResult(result);
        } catch (error) {
            const message =
                (error && error.error && error.error.message) ||
                (error && error.data && error.data.message) ||
                (error && error.message) ||
                _t("The bag label could not be added.");
            this.notification.add(message, {
                title: _t("Scan Bag Label Barcode"),
                type: "danger",
                className:
                    "o_cdu_scan_notification o_cdu_scan_notification_danger",
            });
            await this._clearAndFocus();
        } finally {
            this._isSubmitting = false;
        }
    }
}

BoxBagLabelScanInputField.template = TextField.template;
BoxBagLabelScanInputField.components = TextField.components;
BoxBagLabelScanInputField.defaultProps = TextField.defaultProps;
BoxBagLabelScanInputField.props = TextField.props;
BoxBagLabelScanInputField.extractProps = TextField.extractProps;
BoxBagLabelScanInputField.supportedTypes = ["text"];

registry.category("fields").add(
    "cdu_box_bag_label_scan_input",
    BoxBagLabelScanInputField
);
