/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import {
    isBarcodeScannerSupported,
    scanBarcode,
} from "@web/webclient/barcode/barcode_scanner";

async function scanBaggingQaQr(env, action) {
    const qaId = action.params && action.params.qa_id;
    if (!qaId) {
        env.services.notification.add(_t("The Bagging / QA record could not be identified."), {
            title: _t("Scan QR Code"),
            type: "danger",
        });
        return;
    }
    if (!isBarcodeScannerSupported()) {
        env.services.notification.add(
            _t("This device or browser does not provide camera access for QR scanning."),
            {
                title: _t("Camera unavailable"),
                type: "warning",
            }
        );
        return;
    }

    try {
        const scannedValue = await scanBarcode("environment");
        if (!scannedValue) {
            return;
        }
        const result = await env.services.orm.call(
            "cdu.bagging.qa",
            "action_validate_label_qr",
            [[qaId], scannedValue]
        );
        if (result) {
            return env.services.action.doAction(result);
        }
    } catch (error) {
        const message =
            (error && error.error && error.error.message) ||
            (error && error.data && error.data.message) ||
            (error && error.message) ||
            _t("The QR code could not be scanned.");
        env.services.notification.add(message, {
            title: _t("Scan QR Code"),
            type: "danger",
        });
    }
}

registry.category("actions").add("cdu_bagging_qa_scan_qr", scanBaggingQaQr);
