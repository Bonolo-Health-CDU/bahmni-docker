/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { TextField } from "@web/views/fields/text/text_field";

const SCAN_DEBOUNCE_MS = 350;
const SUCCESS_NAVIGATION_DELAY_MS = 1500;

export class BaggingQaQrInputField extends TextField {
    setup() {
        super.setup();
        this.action = useService("action");
        this.notification = useService("notification");
        this.orm = useService("orm");
        this._isValidatingScan = false;
        this._lastSubmittedScan = "";
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
        this._scheduleScanValidation();
    }

    _focusScanInput() {
        if (!this.textareaRef.el || this.props.readonly) {
            return;
        }
        this.textareaRef.el.focus();
        this.textareaRef.el.select();
    }

    _onScanKeydown(ev) {
        if (ev.key === "Escape") {
            this._clearScanInput();
            return;
        }
        if (ev.key === "Enter" && this._hasCompleteScan(ev.currentTarget.value)) {
            ev.preventDefault();
            ev.stopPropagation();
            this._validateScannedValue(ev.currentTarget.value);
        }
    }

    _scheduleScanValidation() {
        browser.clearTimeout(this._scanTimer);
        const value = this.textareaRef.el ? this.textareaRef.el.value : "";
        if (!this._hasCompleteScan(value)) {
            return;
        }
        this._scanTimer = browser.setTimeout(
            () => this._validateScannedValue(value),
            SCAN_DEBOUNCE_MS
        );
    }

    _hasCompleteScan(value) {
        const scanLines = String(value || "")
            .replace(/\r\n/g, "\n")
            .split("\n")
            .map((line) => line.trim())
            .filter(Boolean);
        return scanLines.length >= 2 && scanLines[0].length >= 2 && scanLines[1].length >= 5;
    }

    async _clearScanInput() {
        if (this.textareaRef.el) {
            this.textareaRef.el.value = "";
        }
        await this.props.update(false);
        this._focusScanInput();
    }

    _wait(ms) {
        return new Promise((resolve) => browser.setTimeout(resolve, ms));
    }

    async _handleScanResult(result) {
        if (!result) {
            return;
        }

        if (result.type === "ir.actions.client" && result.tag === "display_notification") {
            const params = result.params || {};
            const nextAction = params.next;
            this.notification.add(
                params.message || _t("Bagging / QA was confirmed."),
                {
                    title: params.title || _t("QA Passed"),
                    type: params.type || "success",
                    sticky: Boolean(params.sticky),
                    className:
                        params.className ||
                        "o_cdu_scan_notification o_cdu_scan_notification_success",
                }
            );

            if (nextAction) {
                await this._wait(SUCCESS_NAVIGATION_DELAY_MS);
                return this.action.doAction(nextAction);
            }
            return;
        }

        return this.action.doAction(result);
    }

    async _validateScannedValue(value) {
        const scannedValue = String(value || "").trim();
        const qaId = this.props.record && this.props.record.resId;
        if (!scannedValue || !qaId || this._isValidatingScan) {
            return;
        }
        if (scannedValue === this._lastSubmittedScan) {
            return;
        }

        this._isValidatingScan = true;
        this._lastSubmittedScan = scannedValue;
        try {
            const result = await this.orm.call("cdu.bagging.qa", "action_validate_label_qr", [
                [qaId],
                scannedValue,
            ]);
            await this._clearScanInput();
            if (result) {
                return this._handleScanResult(result);
            }
        } catch (error) {
            this._lastSubmittedScan = "";
            const message =
                (error && error.error && error.error.message) ||
                (error && error.data && error.data.message) ||
                (error && error.message) ||
                _t("The QR code could not be validated.");
            this.notification.add(message, {
                title: _t("Scan Bag Label QR"),
                type: "danger",
                className:
                    "o_cdu_scan_notification o_cdu_scan_notification_danger",
            });
            await this._clearScanInput();
        } finally {
            this._isValidatingScan = false;
        }
    }
}

BaggingQaQrInputField.template = TextField.template;
BaggingQaQrInputField.components = TextField.components;
BaggingQaQrInputField.defaultProps = TextField.defaultProps;
BaggingQaQrInputField.props = TextField.props;
BaggingQaQrInputField.extractProps = TextField.extractProps;
BaggingQaQrInputField.supportedTypes = ["text"];

registry.category("fields").add("cdu_bagging_qa_qr_input", BaggingQaQrInputField);
