{
    "name": "CDU eLMIS Integration",
    "summary": "OpenLMIS stock management foundation for the CDU workflow",
    "version": "16.0.1.0.0",
    "category": "Healthcare",
    "author": "Ministry of Health",
    "license": "LGPL-3",
    "depends": [
        "cdu_prescription",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/cdu_elmis_defaults.xml",
        "views/cdu_elmis_settings_views.xml",
        "views/cdu_elmis_api_log_views.xml",
        "views/cdu_elmis_stock_cache_views.xml",
        "views/cdu_elmis_batch_views.xml",
        "views/cdu_elmis_menus.xml",
    ],
    "installable": True,
    "application": False,
}
