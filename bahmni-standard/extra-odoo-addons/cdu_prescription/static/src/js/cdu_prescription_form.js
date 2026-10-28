/** @odoo-module **/

import { useEffect, useRef } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { FormController } from "@web/views/form/form_controller";
import { formView } from "@web/views/form/form_view";

export class CduPrescriptionFormController extends FormController {
    setup() {
        super.setup();
        const rootRef = useRef("root");
        useEffect(
            (root) => {
                const keepRegimenFocus = (event) => {
                    const button = event.target.closest(
                        '.o_form_statusbar button[name="action_mark_patient_verified"], ' +
                        '.o_form_statusbar button[name="action_mark_medicine_validated"]'
                    );
                    const input = root.ownerDocument.activeElement;
                    if (
                        event.button === 0 &&
                        button &&
                        root.contains(input) &&
                        input.closest('[name="product_line_ids"] .o_selected_row')
                    ) {
                        // Blurring the row can refocus it and scroll the button away
                        // before mouseup, losing the click. The normal action flow
                        // commits the focused input when it saves the form.
                        event.preventDefault();
                    }
                };
                root.addEventListener("mousedown", keepRegimenFocus);
                return () => root.removeEventListener("mousedown", keepRegimenFocus);
            },
            () => [rootRef.el]
        );
    }
}

registry.category("views").add("cdu_prescription_form", {
    ...formView,
    Controller: CduPrescriptionFormController,
});
