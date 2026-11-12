/** @odoo-module **/

import "@cdu_prescription/js/cdu_prescription_form";
import { click, getFixture, triggerEvent } from "@web/../tests/helpers/utils";
import { makeView, setupViewRegistries } from "@web/../tests/views/helpers";
import { registry } from "@web/core/registry";

QUnit.module("CDU prescription verification", (hooks) => {
    let target;
    let serverData;

    hooks.beforeEach(() => {
        target = getFixture();
        setupViewRegistries();
        serverData = {
            models: {
                "cdu.prescription": {
                    fields: {
                        product_line_ids: {
                            type: "one2many",
                            relation: "cdu.prescription.product.line",
                            string: "Products",
                        },
                    },
                    records: [{ id: 1, product_line_ids: [10] }],
                },
                "cdu.prescription.product.line": {
                    fields: {
                        dosage_instructions: { type: "text", string: "Dosage" },
                    },
                    records: [{ id: 10, dosage_instructions: "Old dosage" }],
                },
            },
        };
    });

    for (const action of ["action_mark_patient_verified", "action_mark_medicine_validated"]) {
        QUnit.test(`${action} keeps the click and saves the focused dosage`, async (assert) => {
            registry.category("services").add("action", {
                start() {
                    return {
                        async doActionButton(params) {
                            assert.strictEqual(params.name, action);
                            assert.step("action");
                        },
                    };
                },
            }, { force: true });
            await makeView({
                type: "form",
                resModel: "cdu.prescription",
                resId: 1,
                serverData,
                arch: `<form js_class="cdu_prescription_form">
                    <header>
                        <button name="${action}" string="Proceed" type="object" confirm="Proceed?"/>
                    </header>
                    <sheet>
                        <field name="product_line_ids">
                            <tree editable="bottom"><field name="dosage_instructions"/></tree>
                        </field>
                    </sheet>
                </form>`,
                mockRPC(route, args) {
                    if (args.method === "write") {
                        assert.strictEqual(
                            args.args[1].product_line_ids[0][2].dosage_instructions,
                            "One tablet daily.\nWith food."
                        );
                        assert.step("save");
                    }
                },
            });
            await click(target, '.o_data_row [name="dosage_instructions"]');
            const input = target.querySelector("textarea");
            input.focus();
            input.value = "One tablet daily.\nWith food.";
            await triggerEvent(input, null, "input");

            const button = target.querySelector(`button[name="${action}"]`);
            const down = new MouseEvent("mousedown", { bubbles: true, cancelable: true });
            button.dispatchEvent(down);
            assert.ok(down.defaultPrevented, "Keep the row from scrolling the button away before mouseup");
            await triggerEvent(button, null, "mouseup");
            await click(button);
            assert.containsOnce(target, ".modal", "The first click opens confirmation");
            assert.verifySteps([], "Nothing is saved or verified before confirmation");

            await click(target, ".modal-footer .btn-primary");
            assert.verifySteps(["save", "action"], "Save the latest dosage before running the action once");
        });
    }
});
