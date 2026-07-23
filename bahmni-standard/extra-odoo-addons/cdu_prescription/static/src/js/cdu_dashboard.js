/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onWillStart, useState } from "@odoo/owl";

export class CduDashboard extends Component {
    setup() {
        this.actionService = useService("action");
        this.orm = useService("orm");
        this.state = useState({
            loading: true,
            data: {
                sections: [],
                warnings: [],
            },
        });

        onWillStart(async () => {
            this.state.data = await this.orm.call(
                "res.users",
                "get_cdu_dashboard_data",
                []
            );
            this.state.loading = false;
        });
    }

    openAction(actionXmlId) {
        if (actionXmlId) {
            this.actionService.doAction(actionXmlId);
        }
    }
}

CduDashboard.template = "cdu_prescription.CduDashboard";

registry.category("actions").add("cdu_dashboard", CduDashboard);
