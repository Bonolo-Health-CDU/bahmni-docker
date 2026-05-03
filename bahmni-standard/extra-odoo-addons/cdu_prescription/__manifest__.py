{
    "name": "CDU Prescription",
    "summary": "Central Dispensing Unit prescription capture and workflow foundation",
    "version": "16.0.1.0.0",
    "category": "Healthcare",
    "author": "Ministry of Health",
    "license": "LGPL-3",
    "depends": [
        "base",
        "mail",
        "product",
        "stock",
        "sale_stock",
        "bahmni_sale",
        "bahmni_stock",
        "bahmni_product",
        "cdu_base"
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/cdu_sequence.xml",
        "views/cdu_collection_point_views.xml",
        "views/cdu_facility_views.xml",
        "views/cdu_batch_views.xml",
        "views/cdu_prescription_views.xml",
        "views/cdu_menus.xml",
    ],
    "installable": True,
    "application": True,
    'assets': {
    'web.assets_backend': [
        "cdu_base/static/src/css/custom_backend.css",
        ],
    },
}
