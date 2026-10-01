{
    "name": "CDU eRegister Integration Client",
    "summary": "Pull eRegister prescriptions from the FHIR repository (via OpenHIM) and publish CDU fulfilment status back",
    "version": "16.0.1.0.0",
    "category": "Healthcare",
    "author": "Ministry of Health",
    "license": "LGPL-3",
    "depends": [
        "mail",
        "cdu_prescription",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/cdu_eregister_defaults.xml",
        "data/cdu_eregister_cron.xml",
        "views/cdu_eregister_api_log_views.xml",
        "views/cdu_prescription_views.xml",
        "views/cdu_eregister_settings_views.xml",
        "views/cdu_eregister_menus.xml",
    ],
    "installable": True,
    "application": False,
}
