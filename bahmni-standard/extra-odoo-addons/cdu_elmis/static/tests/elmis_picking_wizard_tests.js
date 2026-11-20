/** @odoo-module **/

import { validatePickingWizard } from "@cdu_elmis/js/elmis_picking_wizard";
import { click, getFixture } from "@web/../tests/helpers/utils";
import { makeView, setupViewRegistries } from "@web/../tests/views/helpers";
import { registry } from "@web/core/registry";

function selection(overrides = {}) {
    return {
        picking_line_id: [1, "Patient Rounded Medicine"],
        openmrs_drug_name: "Patient Rounded Medicine",
        selected_stock_option_id: [1, "30-tablet bottle"],
        selected_pack_size: 30,
        selected_stock_on_hand: 20,
        required_units: 75,
        required_quantity: 4,
        quantity_picked: 4,
        ...overrides,
    };
}

function validate(...lines) {
    return validatePickingWizard({
        resModel: "cdu.elmis.picking.wizard",
        data: {
            elmis_picking_fulfilment_line_ids: {
                records: lines.map((data) => ({ data })),
            },
        },
    });
}

QUnit.module("CDU patient pack rounding", () => {
    QUnit.test("accept patient-rounded packs and reject pooled or excessive totals", (assert) => {
        assert.ok(validate(selection()).valid, "35 and 40 tablets require four 30-tablet bottles");
        assert.ok(validate(selection({ quantity_picked: 3 })).message.includes("short by 1.00"));
        assert.ok(validate(selection({ quantity_picked: 5 })).message.includes("exceed required packs"));
    });

    QUnit.test("sum packs picked from different lots of the same size", (assert) => {
        const first = selection({ quantity_picked: 2 });
        const second = selection({ quantity_picked: 2, selected_stock_option_id: [2, "Second lot"] });
        assert.ok(validate(first, second).valid);
        assert.ok(validate(first, { ...second, quantity_picked: 1 }).message.includes("short by 1.00"));
        assert.ok(validate(first, { ...second, quantity_picked: 3 }).message.includes("exceed required packs"));
    });

    QUnit.test("use the server's per-patient total after changing pack size", (assert) => {
        const largerPack = selection({ selected_pack_size: 90, required_quantity: 2, quantity_picked: 2 });
        assert.ok(validate(largerPack).valid, "Each patient still needs a separate bottle");
        assert.ok(validate({ ...largerPack, quantity_picked: 1 }).message.includes("short by 1.00"));
        assert.ok(validate(selection()).valid, "Switching back to 30-tablet bottles requires four");
    });

    QUnit.test("retain whole-pack, stock availability and pack-size checks", (assert) => {
        assert.ok(validate(selection({ quantity_picked: 3.5 })).message.includes("whole number"));
        assert.ok(validate(selection({ selected_stock_on_hand: 3 })).message.includes("exceed available packs"));
        assert.ok(validate(selection({ quantity_picked: 2 }), selection({
            selected_pack_size: 90, selected_stock_option_id: [2, "90-tablet bottle"], quantity_picked: 2,
        })).message.includes("same pack size"));
    });

    QUnit.test("Generate Picking List is enabled for the per-patient total", async (assert) => {
        setupViewRegistries();
        const target = getFixture();
        registry.category("services").add("action", {
            start() {
                return {
                    async doActionButton(params) {
                        assert.strictEqual(params.name, "action_confirm");
                        assert.step("generate");
                    },
                };
            },
        }, { force: true });
        await makeView({
            type: "form",
            resModel: "cdu.elmis.picking.wizard",
            resId: 1,
            serverData: {
                models: {
                    "cdu.elmis.picking.wizard": {
                        fields: {
                            elmis_picking_fulfilment_line_ids: {
                                type: "one2many", relation: "cdu.picking.fulfilment.line", string: "Selections",
                            },
                        },
                        records: [{ id: 1, elmis_picking_fulfilment_line_ids: [1] }],
                    },
                    "cdu.picking.fulfilment.line": {
                        fields: {
                            openmrs_drug_name: { type: "char", string: "Regimen name" },
                            picking_line_id: { type: "many2one", relation: "cdu.picking.line", string: "Regimen" },
                            selected_stock_option_id: { type: "many2one", relation: "cdu.elmis.stock.option", string: "Stock" },
                            selected_pack_size: { type: "integer", string: "Pack size" },
                            selected_stock_on_hand: { type: "integer", string: "Available packs" },
                            required_units: { type: "float", string: "Required units" },
                            required_quantity: { type: "float", string: "Required packs" },
                            quantity_picked: { type: "float", string: "Picked packs" },
                        },
                        records: [{ ...selection(), id: 1, picking_line_id: 1, selected_stock_option_id: 1 }],
                    },
                    "cdu.picking.line": {
                        fields: { display_name: { type: "char", string: "Name" } },
                        records: [{ id: 1, display_name: "Patient Rounded Medicine" }],
                    },
                    "cdu.elmis.stock.option": {
                        fields: { display_name: { type: "char", string: "Name" } },
                        records: [{ id: 1, display_name: "30-tablet bottle" }],
                    },
                },
            },
            arch: `<form class="o_cdu_elmis_picking_wizard">
                <header>
                    <button name="action_confirm" type="object" string="Generate Picking List"
                        class="o_cdu_generate_picking_button"/>
                </header>
                <sheet>
                    <field name="elmis_picking_fulfilment_line_ids">
                        <tree>
                            <field name="picking_line_id"/>
                            <field name="selected_stock_option_id"/>
                            <field name="selected_pack_size"/>
                            <field name="selected_stock_on_hand"/>
                            <field name="required_units"/>
                            <field name="required_quantity"/>
                            <field name="quantity_picked"/>
                        </tree>
                    </field>
                    <div class="o_cdu_elmis_picking_client_warning d-none"/>
                </sheet>
            </form>`,
        });
        const button = target.querySelector('button[name="action_confirm"]');
        assert.notOk(button.disabled, "Four bottles can proceed");
        assert.hasClass(target.querySelector(".o_cdu_elmis_picking_client_warning"), "d-none");
        await click(button);
        assert.verifySteps(["generate"], "The generation action is called once");
    });
});
