/** @odoo-module **/

import { registry } from "@web/core/registry";
import { FloatField } from "@web/views/fields/float/float_field";
import { formatFloat } from "@web/views/fields/formatters";

/**
 * Display float values without insignificant trailing zeroes while retaining
 * the configured precision for genuine fractional values.
 *
 * Examples with two-digit precision:
 *   3.00 -> 3
 *   2.50 -> 2.5
 *   2.25 -> 2.25
 */
export class NoTrailingZerosFloatField extends FloatField {
    get formattedValue() {
        if (this.props.inputType === "number" && !this.props.readonly && this.props.value) {
            return this.props.value;
        }
        return formatFloat(this.props.value, {
            digits: this.props.digits,
            noTrailingZeros: true,
        });
    }
}

NoTrailingZerosFloatField.template = FloatField.template;
NoTrailingZerosFloatField.defaultProps = FloatField.defaultProps;
NoTrailingZerosFloatField.props = FloatField.props;
NoTrailingZerosFloatField.extractProps = FloatField.extractProps;
NoTrailingZerosFloatField.supportedTypes = FloatField.supportedTypes;

registry.category("fields").add(
    "cdu_no_trailing_zeros_float",
    NoTrailingZerosFloatField
);
