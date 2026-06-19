/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";

async function printBoxDocuments(env, action) {
    const params = action.params || {};
    const reports = params.report_actions || [];

    try {
        for (const reportAction of reports) {
            await env.services.action.doAction(reportAction);
        }
        if (params.message) {
            env.services.notification.add(params.message, {
                title: params.title || _t("Box documents ready"),
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
            _t("The box documents could not be generated.");
        env.services.notification.add(message, {
            title: _t("Box document printing"),
            type: "danger",
        });
    }
}

registry.category("actions").add("cdu_print_box_documents", printBoxDocuments);
