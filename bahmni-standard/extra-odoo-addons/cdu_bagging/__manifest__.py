{
    "name": "CDU Bagging",
    "summary": "Bag barcode scanning and picking bag status tracking",
    "version": "16.0.1.0.0",
    "category": "Inventory/Inventory",
    "author": "Ministry of Health",
    "license": "LGPL-3",
    "depends": ["stock", "mail"],
    "data": [
        "security/ir.model.access.csv",
        "data/bagging_sequences.xml",
        "views/bagging_views.xml",
        "views/stock_picking_views.xml",
    ],
    "installable": True,
    "application": False,
}
