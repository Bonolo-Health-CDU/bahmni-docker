/** @odoo-module **/

import { Dropdown } from "@web/core/dropdown/dropdown";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onMounted, onWillStart, onWillUnmount, useState } from "@odoo/owl";

const { DateTime } = luxon;
const REFRESH_MS = 60 * 1000;
// "Sync is behind" after two missed scheduled pulls, and never under 10 minutes.
const staleAfterMinutes = (interval) => Math.max(10, 2 * (interval || 2));

/**
 * "EMR Sync" in the top bar: shows how the eRegister prescription pull is
 * doing and lets CDU staff pull new prescriptions on demand.
 */
export class EmrSyncSystray extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({ status: null, pulling: false });

        onWillStart(() => this.refresh());
        onMounted(() => {
            this.timer = setInterval(() => this.refresh(), REFRESH_MS);
        });
        onWillUnmount(() => clearInterval(this.timer));
    }

    async refresh() {
        try {
            this.state.status = await this.orm.call("cdu.eregister.sync", "get_sync_status", []);
        } catch {
            // Keep the last known status; the next refresh will try again.
        }
    }

    // ------------------------------------------------------------ display
    get status() {
        return this.state.status || {};
    }

    get busy() {
        return this.state.pulling || this.status.running;
    }

    /** Overall health, shown as the dot colour and the panel's badge. */
    get health() {
        const status = this.status;
        if (!status.configured) {
            return { level: "off", label: "Not configured" };
        }
        if (status.last_pull && !status.last_pull.ok) {
            return { level: "error", label: "Last sync failed" };
        }
        if (!status.last_successful_pull) {
            return { level: "warn", label: "Never synced" };
        }
        const minutes = -DateTime.fromISO(status.last_successful_pull.at).diffNow("minutes").minutes;
        if (status.enabled && minutes > staleAfterMinutes(status.pull_interval_minutes)) {
            return { level: "warn", label: "Sync is behind" };
        }
        if (!status.enabled) {
            return { level: "off", label: "Automatic sync off" };
        }
        return { level: "ok", label: "Up to date" };
    }

    get scheduleLabel() {
        const minutes = this.status.pull_interval_minutes;
        if (!minutes) {
            return "On schedule";
        }
        if (minutes === 1) {
            return "Every minute";
        }
        if (minutes % 60 === 0) {
            const hours = minutes / 60;
            return hours === 1 ? "Every hour" : `Every ${hours} hours`;
        }
        return `Every ${minutes} minutes`;
    }

    relative(iso) {
        return iso ? DateTime.fromISO(iso).toRelative() : "never";
    }

    absolute(iso) {
        return iso ? DateTime.fromISO(iso).toLocaleString(DateTime.DATETIME_MED_WITH_SECONDS) : "";
    }

    triggerLabel(pull) {
        if (!pull) {
            return "";
        }
        return pull.trigger === "manual" ? `by ${pull.user || "a user"}` : "automatically";
    }

    // ------------------------------------------------------------ actions
    async pullNow() {
        if (this.busy) {
            return;
        }
        this.state.pulling = true;
        try {
            const result = await this.orm.call("cdu.eregister.sync", "action_pull_now", []);
            this.state.status = result.status;
            const created = result.outcome ? result.outcome.created : 0;
            this.notification.add(result.message, {
                title: "eRegister sync",
                type: result.busy ? "info" : !result.ok ? "danger" : created ? "success" : "info",
                buttons: created
                    ? [
                          {
                              name: "Review new prescriptions",
                              primary: true,
                              onClick: () =>
                                  this.action.doAction("cdu_prescription.action_cdu_prescription_awaiting_verification"),
                          },
                      ]
                    : [],
            });
        } catch (error) {
            this.notification.add(error.message || "The sync could not be started.", {
                title: "eRegister sync",
                type: "danger",
            });
        } finally {
            this.state.pulling = false;
        }
    }

    openPrescription(id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "cdu.prescription",
            res_id: id,
            views: [[false, "form"]],
        });
    }

    openPublicationFailures() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "eRegister status not published",
            res_model: "cdu.prescription",
            views: [
                [false, "list"],
                [false, "form"],
            ],
            domain: [["fhir_sync_state", "=", "failed"]],
        });
    }

    openSettings() {
        this.action.doAction("cdu_eregister.action_cdu_eregister_settings");
    }

    openLog() {
        this.action.doAction("cdu_eregister.action_cdu_eregister_api_log");
    }
}

EmrSyncSystray.template = "cdu_eregister.EmrSyncSystray";
EmrSyncSystray.components = { Dropdown };

// Sequence 1: directly left of the user menu (sequence 0) in the top bar.
registry.category("systray").add("cdu_eregister.emr_sync", { Component: EmrSyncSystray }, { sequence: 1 });
