/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";

async function printDispensingLabelsAndContinue(env, action) {
    const params = action.params || {};
    const reportAction = params.report_action;

    try {
        if (reportAction) {
            await env.services.action.doAction(reportAction);
        }
        if (params.message) {
            env.services.notification.add(params.message, {
                title: params.title || _t("Dispensing confirmed"),
                type: params.notification_type || "success",
                sticky: Boolean(params.sticky),
            });
        }
        if (params.next) {
            return env.services.action.doAction(params.next);
        }
    } catch (error) {
        const message =
            (error && error.data && error.data.message) ||
            (error && error.message) ||
            _t("The dispensing labels could not be generated.");
        env.services.notification.add(message, {
            title: _t("Dispensing label printing"),
            type: "danger",
        });
    }
}

registry.category("actions").add(
    "cdu_print_dispensing_labels_and_continue",
    printDispensingLabelsAndContinue
);
