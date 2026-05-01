{
    "name": "CDU Base",
    "summary": "Central Dispensing Unit branding customizations",
    "version": "16.0.1.0.0",
    "category": "Localization",
    "author": "Ministry of Health",
    "license": "LGPL-3",
    "depends": ["base", "web", "cdu_patient_management"],
    "data": [
        "views/login_template.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "cdu_base/static/src/css/custom_backend.css",
        ],
        "web.assets_frontend": [
            "cdu_base/static/src/css/custom_frontend.css",
        ],
    },
    "installable": True,
    "application": False,
}
