/** @odoo-module **/

import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onWillStart, useState } from "@odoo/owl";

export class CduAllocationBoard extends Component {
    setup() {
        this.actionService = useService("action");
        this.notification = useService("notification");
        this.orm = useService("orm");
        this.pickingLineId = this.props.action.params.picking_line_id;
        this.history = [];
        this.redoHistory = [];
        this.state = useState({
            loading: true,
            saving: false,
            data: {
                components: [],
                patients: [],
                summary: {},
                readiness_errors: [],
            },
            reason: "",
            selectedLots: {},
            historyCount: 0,
            redoCount: 0,
        });

        onWillStart(() => this.load());
    }

    async call(method, extraArgs = []) {
        return this.orm.call(
            "cdu.picking.line",
            method,
            [[this.pickingLineId], ...extraArgs]
        );
    }

    async load() {
        this.state.loading = true;
        try {
            const data = await this.call("get_allocation_board_data");
            this.setData(data);
        } finally {
            this.state.loading = false;
        }
    }

    setData(data) {
        this.state.data = data;
        this.state.reason = data.override_reason || "";
    }

    syncHistoryCounts() {
        this.state.historyCount = this.history.length;
        this.state.redoCount = this.redoHistory.length;
    }

    inverseChanges(changes) {
        return [...changes].reverse().map((change) => ({
            ...change,
            delta: -change.delta,
        }));
    }

    async runUpdate(callback) {
        if (this.state.saving || this.state.data.locked) {
            return false;
        }
        this.state.saving = true;
        try {
            const data = await callback();
            this.setData(data);
            return data;
        } catch (error) {
            this.notification.add(
                error.message || "The allocation could not be updated.",
                {
                    title: "Allocation not changed",
                    type: "danger",
                    sticky: true,
                }
            );
            return false;
        } finally {
            this.state.saving = false;
        }
    }

    async applyChanges(changes, recordHistory = true) {
        const data = await this.runUpdate(() =>
            this.call("action_update_allocation_board", [changes])
        );
        if (data && recordHistory) {
            this.history.push({
                forward: changes,
                inverse: this.inverseChanges(changes),
            });
            this.redoHistory = [];
            this.syncHistoryCounts();
        }
        return data;
    }

    adjust(resolutionId, groupId, stockOptionId, delta) {
        return this.applyChanges([
            {
                resolution_id: resolutionId,
                group_id: groupId,
                stock_option_id: stockOptionId,
                delta,
            },
        ]);
    }

    selectLot(groupId, stockOptionId, remainingPacks, status) {
        if (status === "expired" || !remainingPacks) {
            this.notification.add(
                status === "expired"
                    ? "Expired stock cannot be allocated."
                    : "All selected packs from this lot are already allocated.",
                {
                    title: "Stock lot unavailable",
                    type: "warning",
                }
            );
            return;
        }
        this.state.selectedLots[groupId] = stockOptionId;
    }

    selectedLot(groupId) {
        return this.state.selectedLots[groupId] || false;
    }

    addSelectedLot(resolutionId, groupId) {
        const stockOptionId = this.selectedLot(groupId);
        if (!stockOptionId) {
            this.notification.add("Select a stock lot for this component first.", {
                title: "Choose stock",
                type: "warning",
            });
            return;
        }
        return this.adjust(resolutionId, groupId, stockOptionId, 1);
    }

    startLotDrag(event, groupId, stockOptionId) {
        event.dataTransfer.effectAllowed = "copy";
        event.dataTransfer.setData(
            "application/json",
            JSON.stringify({
                kind: "stock",
                group_id: groupId,
                stock_option_id: stockOptionId,
            })
        );
    }

    startAllocationDrag(
        event,
        resolutionId,
        groupId,
        stockOptionId
    ) {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData(
            "application/json",
            JSON.stringify({
                kind: "allocation",
                resolution_id: resolutionId,
                group_id: groupId,
                stock_option_id: stockOptionId,
            })
        );
    }

    allowDrop(event) {
        event.preventDefault();
    }

    dragData(event) {
        try {
            return JSON.parse(
                event.dataTransfer.getData("application/json") || "{}"
            );
        } catch (_error) {
            return {};
        }
    }

    dropOnPatient(event, resolutionId, groupId) {
        event.preventDefault();
        const payload = this.dragData(event);
        if (
            !payload.stock_option_id ||
            Number(payload.group_id) !== Number(groupId)
        ) {
            this.notification.add(
                "Packs can only be moved within their regimen component.",
                { title: "Different component", type: "warning" }
            );
            return;
        }
        const changes = [];
        if (
            payload.kind === "allocation" &&
            Number(payload.resolution_id) !== Number(resolutionId)
        ) {
            changes.push({
                resolution_id: payload.resolution_id,
                group_id: groupId,
                stock_option_id: payload.stock_option_id,
                delta: -1,
            });
        }
        if (
            payload.kind === "stock" ||
            Number(payload.resolution_id) !== Number(resolutionId)
        ) {
            changes.push({
                resolution_id: resolutionId,
                group_id: groupId,
                stock_option_id: payload.stock_option_id,
                delta: 1,
            });
        }
        if (changes.length) {
            return this.applyChanges(changes);
        }
    }

    dropOnStock(event, groupId, stockOptionId) {
        event.preventDefault();
        const payload = this.dragData(event);
        if (
            payload.kind !== "allocation" ||
            Number(payload.group_id) !== Number(groupId) ||
            Number(payload.stock_option_id) !== Number(stockOptionId)
        ) {
            return;
        }
        return this.adjust(
            payload.resolution_id,
            groupId,
            stockOptionId,
            -1
        );
    }

    async addNextBundle(resolutionId) {
        const data = await this.runUpdate(() =>
            this.call("action_add_next_pack_bundle", [resolutionId])
        );
        const changes = (data && data.applied_changes) || [];
        if (changes.length) {
            this.history.push({
                forward: changes,
                inverse: this.inverseChanges(changes),
            });
            this.redoHistory = [];
            this.syncHistoryCounts();
        }
    }

    async undo() {
        const entry = this.history.pop();
        if (!entry) {
            return;
        }
        const data = await this.applyChanges(entry.inverse, false);
        if (data) {
            this.redoHistory.push(entry);
        } else {
            this.history.push(entry);
        }
        this.syncHistoryCounts();
    }

    async redo() {
        const entry = this.redoHistory.pop();
        if (!entry) {
            return;
        }
        const data = await this.applyChanges(entry.forward, false);
        if (data) {
            this.history.push(entry);
        } else {
            this.redoHistory.push(entry);
        }
        this.syncHistoryCounts();
    }

    async resetRecommended() {
        if (
            !browser.confirm(
                "Replace all manual changes with the Balanced FEFO proposal?"
            )
        ) {
            return;
        }
        const data = await this.runUpdate(() =>
            this.call("action_reset_balanced_allocation")
        );
        if (data) {
            this.history = [];
            this.redoHistory = [];
            this.syncHistoryCounts();
        }
    }

    saveReason() {
        return this.runUpdate(() =>
            this.call("action_set_manual_override_reason", [
                this.state.reason,
            ])
        );
    }

    async backToRegimen() {
        const action = await this.call(
            "action_open_bulk_allocation_overview"
        );
        return this.actionService.doAction(action);
    }

    async confirmPicking() {
        if (
            !this.state.data.can_confirm ||
            !browser.confirm(
                "Confirm picking, lock all allocations, and move the selected stock to the Production Floor?"
            )
        ) {
            return;
        }
        this.state.saving = true;
        try {
            const action = await this.call("action_confirm_picking");
            return this.actionService.doAction(action);
        } finally {
            this.state.saving = false;
        }
    }
}

CduAllocationBoard.template = "cdu_elmis.AllocationBoard";

registry
    .category("actions")
    .add("cdu_elmis_allocation_board", CduAllocationBoard);
