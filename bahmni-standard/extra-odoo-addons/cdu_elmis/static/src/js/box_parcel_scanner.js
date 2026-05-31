/** @odoo-module **/

import { browser } from "@web/core/browser/browser";
import { patch } from "@web/core/utils/patch";
import { Many2OneField } from "@web/views/fields/many2one/many2one_field";

patch(Many2OneField.prototype, "cdu_elmis.BoxParcelScannerMany2OneField", {
    setup() {
        this._super(...arguments);
        const originalUpdate = this.update;
        this.update = async (value, params = {}) => {
            const shouldAutoAddLine = this.shouldAutoAddBoxParcelLine(value);
            const result = await originalUpdate.call(this, value, params);
            if (shouldAutoAddLine) {
                this.scheduleBoxParcelLineAdd();
            }
            return result;
        };
    },

    shouldAutoAddBoxParcelLine(value) {
        return (
            this.props.name === "bagging_qa_id" &&
            this.props.record.resModel === "cdu.box.line" &&
            this.props.record.isNew &&
            Boolean(value)
        );
    },

    scheduleBoxParcelLineAdd() {
        browser.setTimeout(() => {
            const list = this.autocompleteContainerRef.el.closest(".o_field_x2many_list");
            const addLine = list && list.querySelector(".o_field_x2many_list_row_add a");
            if (!addLine) {
                return;
            }
            addLine.click();
            browser.setTimeout(() => {
                const nextInput = list.querySelector(
                    ".o_selected_row td[name='bagging_qa_id'] input"
                );
                if (nextInput) {
                    nextInput.focus();
                }
            });
        });
    },
});
