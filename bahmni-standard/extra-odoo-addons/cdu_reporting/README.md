# CDU Reporting

`cdu_reporting` is the shared reporting layer for the Bonolo Health Central
Dispensing Unit application on Odoo 16. It provides six native Odoo reports,
one common filter wizard, XLSX/CSV exports, concise QWeb PDF summaries, and an
immutable prescription lifecycle audit.

## Architecture

Transactional data remains the source of truth. No report copies transaction
rows into a reporting table.

1. `cdu_reporting_prescription_fact` is a PostgreSQL view at one row per
   prescription. CTEs aggregate rejection, expected medicine, dispensed
   medicine, parcel and box data before those datasets are joined.
2. Six Odoo `_auto = False` models expose purpose-specific views.
3. Native list, search, pivot and graph views query those analytical models.
4. `cdu.report.wizard` builds one domain and uses it for interactive views,
   XLSX, CSV and PDF. This keeps exports and screen results reconciled.

All analytical models reject create, write and unlink operations.

## Source mapping

| Report | Source models | Reporting model | Main date | Main measures |
|---|---|---|---|---|
| Patient Enrolment | `cdu.prescription`, `res.partner`, `cdu.facility`, `cdu.collection.point` | `cdu.report.enrolment` | Prescription date (enrolment proxy) | Total, new, repeat |
| Prescription Verification | `cdu.prescription`, rejection and lifecycle history | `cdu.report.verification` | Receipt/create date | Received, verified, submitted, rejected |
| Prescription Validation | `cdu.prescription`, rejection and lifecycle history | `cdu.report.validation` | Verified date | Awaiting, validated, released, rejected |
| Dispensing | Prescription, `cdu.dispense`, stock selections, picking lines, box/Collect-and-Go | `cdu.report.dispensing` | Production receipt | Prescriptions, items, units, fulfilment/status |
| Parcel Production | `cdu.bagging.qa`, prescription, box and PUP | `cdu.report.parcel.production` | Bagging/QA confirmation | Produced, pending, dispatched |
| Parcel Forecast | Prescription schedule and actual QA parcel | `cdu.report.parcel.forecast` | Next drug pickup date | Patients due, forecast, actual, variance |

## Field semantics and current source limitations

- The application has no separate enrolment transaction. `prescription_date`
  is the enrolment date and `new_or_revisit` determines new/repeat/restarted.
- `hiv_program_id` is the only available programme dimension. It is exposed as
  `programme`; no duplicate programme master was introduced.
- There is no facility-district relation or district master. District uses the
  synced PUP `region_name`, falling back to prescription `e_locker_district`.
- PUP means `cdu.collection.point`; PUP type is `point_type`.
- The forecast uses `next_drug_pickup_date`. This is the currently persisted
  date on which the next CDU supply is due and is more suitable than the next
  clinical appointment date for parcel workload.
- Collection and return are not local transactions. They are mapped from
  Collect-and-Go parcel statuses containing “collect” or “return”, with the
  status receipt timestamp as the event date.
- Patient age is calculated on the prescription date. Age bands are 0–4,
  5–14, 15–24, 25–34, 35–44, 45–54, 55–64 and 65+.

## Status mappings

Verification:

- no `verified_at`: Awaiting Verification;
- active rejection from verification: Rejected;
- verified and currently awaiting validation: Submitted for Validation;
- verified and progressed beyond validation intake: Verified.

Validation:

- verified but no `validated_at`: Awaiting Validation;
- active rejection from validation: Rejected;
- validated and currently awaiting batching: Released for Batching;
- validated and progressed further: Validated.

Dispensing status uses the furthest observable outcome in this precedence:
Returned, Collected, Dispatched, active dispensing rejection, Dispensed, then
Received for Production.

## Lifecycle history

`cdu.prescription.status.history` records previous/new state, timestamp, actor,
reason and remarks. `cdu.prescription.create()` records the initial state and
every ORM state write records a new immutable event. Reporting installation
backfills only events that have trustworthy timestamps in existing data:
creation, verification, validation, picking completion, dispensing, bagging,
boxing and dispatch. It does not manufacture timestamps for unrecoverable
historical events.

History writes and deletes are blocked. State updates must continue through
the prescription ORM so that history remains complete.

## Measures and reconciliation rules

- A prescription count always uses the one-row-per-prescription report grain.
- A parcel count always uses one confirmed `cdu.bagging.qa` record.
- Dispensed item count is the number of positive stock-selection lines.
- Total quantity dispensed is the sum of `selected_pack_size ×
  quantity_dispensed` (`dispensed_units`).
- Fully fulfilled requires confirmed dispensing, a positive quantity on every
  selection, and at least as many distinct dispensed items as expected picking
  items. A dispense record not meeting that condition is partially fulfilled.
- Forecasted parcels and patients due are one per scheduled prescription.
- Actual forecast parcels are confirmed QA parcels linked to those scheduled
  prescriptions. Variance is actual minus forecast.
- Verification rate is verified plus submitted-for-validation divided by all
  verification records in the filtered dataset.
- Validation rate is validated plus released-for-batching divided by all
  validation records in the filtered dataset.
- Dispensing rate is all prescriptions at or beyond dispensing divided by all
  production-received prescriptions in the filtered dataset.
- Collection rate is collected divided by prescriptions at or beyond dispatch.
- All percentage calculations return zero for a zero denominator.

## Filters and views

Every report has a search view with applicable fields, status shortcuts, date
filters and Group By choices. Odoo Favorites provide saved filters without a
custom persistence layer. Pivot measures sum pre-aggregated report columns;
graphs use non-sensitive organisation/status/time dimensions.

The common wizard supports reporting period, district text, facilities,
programme text, PUP type, PUPs, gender, age and report-specific status. Filters
that do not apply to a selected report are ignored.

## Exports

- XLSX is the primary detail format and uses the installed `xlsxwriter`
  library. Workbooks contain Summary, Detailed Records, By Facility, By
  District and By PUP sheets.
- CSV is UTF-8 with BOM, includes understandable headers, and omits technical
  IDs.
- PDF is a concise management summary with KPIs and district/facility totals;
  patient-level rows are intentionally excluded.

All formats call the same `_build_domain()` and query through the current
user's environment. Exports never use `sudo()`.

## Security

- `Reporting User`: read analytical/history models and generate reports.
- `Reporting Manager`: implies Reporting User.
- CDU Admin implies Reporting Manager.
- All analytical ACLs are read-only; wizard records are transient.

The current transactional application does not model authorised facility or
district sets on users. Therefore no invented facility rule is applied.
Reporting access must be assigned only to authorised staff. If facility or
district scope fields are introduced later, add equivalent record rules to all
six report models and test exports with those users.

## Performance

The fact view pre-aggregates one-to-many sources before joins. It never joins
raw medicine lines to raw lifecycle or parcel rows. Targeted indexes cover
prescription status, reporting/forecast dates, facility, PUP, and operational
confirmation dates. PostgreSQL `EXPLAIN (ANALYZE, BUFFERS)` should be used with
production-sized data before changing indexes.

## Testing

Run the tagged suite with:

```bash
odoo -d odoo -u cdu_reporting --test-enable \
  --test-tags /cdu_reporting --stop-after-init --no-http
```

Tests create reconciled prescriptions for awaiting, verified, submitted,
validated, released and rejected stages, a two-medicine dispense, parcel,
dispatch and collection. They verify all six SQL models, no medicine-line
double counting, lifecycle immutability, common filters, and XLSX/CSV parity.

## Adding a report

1. Define and document the transactional grain and main reporting date.
2. Add pre-aggregation CTEs to the shared fact only if the data is reusable.
3. Create a read-only SQL view and `_auto = False` model.
4. Add ACL, list/search/pivot/graph views and menu action.
5. Add one `REPORT_CONFIG` entry so the common wizard and exporters can use it.
6. Add reconciliation tests proving list, pivot measures and exports use the
   same filtered dataset.

