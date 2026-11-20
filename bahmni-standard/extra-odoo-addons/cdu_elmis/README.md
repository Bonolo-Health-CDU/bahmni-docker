# CDU eLMIS Integration

Production at the CDU, from picking to dispatch, and the two outside systems involved:

- **eLMIS (OpenLMIS)**, which holds the CDU's stock.
- **Collect & Go**, which delivers parcels to pickup points and reports what happens to them.

This module also queues the labels and box manifests that the [print agent](../../print-agent/README.md) prints.

It builds on [`cdu_prescription`](../cdu_prescription/README.md), which brings prescriptions in and batches them.

## Production workflow

| Prescription state | Step | Screen | Moves on when |
|---|---|---|---|
| Awaiting picking | Choose lots from eLMIS store stock for the whole batch and confirm picking, then print the picking list (a PDF from the browser) | Operations › Workload Batches | Picking is confirmed in eLMIS |
| Awaiting dispensing | Choose production-floor lots for one prescription, then confirm. Confirming queues the medicine and bag labels. | Production Floor › Awaiting Dispensing | Dispensing is confirmed |
| Awaiting bagging / QA | Scan the QR code on the bag label. It must match both the patient and the prescription. | Production Floor › Bagging / QA Work Queue | QA is confirmed |
| Awaiting boxing | Scan bag barcodes into a box, then confirm. Confirming queues the box label and manifest. | Production Floor › Boxing | The box is confirmed |
| Awaiting dispatch | **Confirm Dispatch Handover** sends the box's parcels to Collect & Go | Dispatch › Dispatch Work Queue | Collect & Go has processed the message |
| Dispatched | Collect & Go reports parcel statuses (see below) | Records › Boxes | Collected, or returned uncollected |

A prescription can also be rejected during dispensing, for example *Out of Stock*; see Rejections in `cdu_prescription`. Dispensing can be partial: the record keeps the back-order details.

The parcel reference is reserved when the bag label is first printed. It is the barcode on the bag and the parcel's external reference at Collect & Go, so a reprint reuses it.

## eLMIS stock

The CDU has two eLMIS facilities: the **store** and the **production floor**. Odoo posts a stock event at three points:

| When | eLMIS stock event | Reasons (defaults) |
|---|---|---|
| Picking confirmed (batch) | Debit the store, credit the production floor | Internal Transfer Out / Internal Transfer In |
| Box confirmed | Consume from the production floor | Consumed |
| Residual stock returned (batch) | Debit the production floor, credit the store | Internal Transfer Out / Internal Transfer In |

Stock events are posted with the eLMIS identity of the person doing the work. When their eLMIS session has expired, Odoo asks them to sign in to eLMIS again before continuing.

The **medicine catalogue** maps eLMIS orderables to Odoo products. Pack-size variants are merged into one generic medicine, and quantities are converted to packs only at picking. A daily job refreshes the catalogue, and stock levels are cached for five minutes.

## Collect & Go

| Job | Default | What it does |
|---|---|---|
| CDU Collect-and-Go Message Poll | Every minute | Checks whether Collect & Go has processed a submitted box. When it has, the box's prescriptions become *Dispatched*. |
| CDU Collect-and-Go Status Poll | Every 15 minutes | Reads parcel tracking statuses |

A failed submission can be retried from the box, up to `cdu.collect_go.max_retry_count` times (default 3). Every call is recorded in Configuration › Collect-and-Go API Logs.

Each parcel stores its latest tracking status and time. An older status never replaces a newer one, and a repeated status isn't acted on again. Only confirmed outcomes change the prescription (`models/collect_go_status.py`):

| Collect & Go code | Effect |
|---|---|
| 12 Collected by Customer | Prescription → **Collected** (final) |
| 11 Returned to CDU (Confirmed), 16 Returned to Vendor | Prescription → **Returned uncollected** (final). In Collect & Go's terms the "vendor" is the CDU. |
| 8, 9 Delivered to the wrong location; 15 Ready for Vendor Collection | Recorded, and an activity is raised for a CDU admin |
| All others, for example in transit or at the pickup point | Recorded for information |

Collect & Go sends local Maseru time without a time zone; `parse_status_date` converts it to UTC.

When [`cdu_eregister`](../cdu_eregister/README.md) is installed, each of these state changes is also published to eRegister.

## Printing

Print jobs (`bahmni.print.job`) are queued as *Pending*, picked up by the print agent, and marked *Done* or *Failed*. The agent signs in as a user in the **CDU Print Agent** group. Documents:

- medicine labels and bag label (dispensing)
- box label and manifest PDF (boxing)

The picking list isn't queued: it opens as a PDF in the browser.

Label layouts are `bahmni.label.template` records.

## Configuration

CDU › Configuration › eLMIS Settings (admin) holds the eLMIS and Collect & Go connections:

- **eLMIS:** base URL, client credentials, the store and production-floor facility codes, the programme, and the stock reason names. Odoo looks up the reason IDs from the names.
- **Collect & Go:** base URL, timeout, the CDU's Collect & Go location, and the tracking status sent at dispatch.

Use **Test Collect-and-Go** to check the connection. Calls are logged under Configuration › eLMIS API Logs and Collect-and-Go API Logs.

`data/cdu_elmis_defaults.xml` ships placeholder credentials, for example a `changeme` client secret. Set real values on every instance.

## Tests

```sh
odoo -d <throwaway-db> -i cdu_elmis --without-demo=all --test-tags /cdu_elmis --stop-after-init --no-http
```

| File | Covers |
|---|---|
| `test_cdu_dispensing.py` | Catalogue sync, picking and pack rounding, dispensing, labels, bag label QR checks, the print agent API |
| `test_cdu_boxing.py` | Scanning parcels into boxes, box documents and manifest printing |
| `test_collect_go_statuses.py` | Tracking status parsing, which statuses move a prescription, admin activities |

The tests use fakes for eLMIS and Collect & Go; they make no network calls.
