# CDU Prescription

The foundation of the Central Dispensing Unit in Odoo. It brings prescriptions in, takes them through verification and validation, groups them into workload batches, and defines the CDU's roles, menus and reference data.

Production after batching (picking, dispensing, bagging and QA, boxing, dispatch) is in [`cdu_elmis`](../cdu_elmis/README.md). Publishing status to eRegister is in [`cdu_eregister`](../cdu_eregister/README.md).

## Roles

| Group | Works on |
|---|---|
| CDU Data Clerk | Intake and verification |
| CDU Call Agent | Prescriptions rejected to the call center |
| CDU Dispensing Officer | Validation, batches and production |
| CDU Admin | Everything, plus cancellation and the integration settings |

Each role sees only its own queues. Record rules in `security/cdu_security.xml` limit prescriptions and rejection history by role.

**Default users.** `data/cdu_default_users.xml` creates one user per role (`cdu.data.clerk`, `cdu.call.agent`, `cdu.dispensing.officer`, `cdu.admin`) with a shared, known password. On any instance other than a throwaway development database, change those passwords or archive the users.

## Where prescriptions come from

| Source | How | Where |
|---|---|---|
| eRegister report file (CSV or XLSX) | Uploaded one at a time or several at once | Prescriptions › Intake › Report Imports / Import Multiple Reports |
| OneDrive | Polled for new report files. Each facility has a folder named exactly like its Facility record; unmatched folders are listed in the poll log. | Configuration › OneDrive Sources (admin) |
| eRegister over FHIR | Pulled from the prescription repository by `cdu_eregister` | See its README |

A report row becomes one `cdu.prescription`. The unique `source_key` means a row imported twice is not created twice. Each import is kept as a report run with its rows, so a clerk can see which rows failed and why.

## Workflow

| State | Who acts | Next |
|---|---|---|
| Awaiting verification | Data clerk checks the patient and regimen products, then **Mark Patient Verified** | Awaiting validation |
| Awaiting validation | Dispensing officer checks the pickup point, products, dosage and next pickup date, then **Mark Medicine Validated** | Awaiting batching |
| Awaiting batching | Dispensing officer adds it to a workload batch and confirms the batch | Awaiting picking |
| Awaiting picking … dispatched, collected, returned uncollected | `cdu_elmis` | |
| Cancelled | Admin only | Final |

Verification and validation list everything missing before they let a prescription move on.

### Rejections

A prescription can be rejected from verification, validation or dispensing:

- **To the call center** when the patient must be contacted.
- **To the facility** when the prescriber must correct it.

The rejection wizard requires at least one reason from `cdu.rejection.reason` (seeded by `data/cdu_rejection_reasons.xml`). *Out of Stock* is allowed only during dispensing. Every rejection is kept in the rejection history.

**Return to Previous Stage** sends the prescription back to the stage it was rejected from and closes the active rejection.

### Days of supply

| Field | Calculation |
|---|---|
| Facility days | Next drug pickup date − prescription date |
| CDU days | Next clinical visit date − next drug pickup date |
| Total days | Facility days + CDU days |

## Workload batches

Operations › Workload Batches. A batch selects prescriptions awaiting batching by pickup date range, e-locker district or collection point. The officer sets repeat days per regimen, then **Confirm** moves the prescriptions to *Awaiting picking*. `cdu_elmis` extends batches with eLMIS stock, the picking list and residual returns.

Batch states: Draft → Confirmed → Picking Generated → Picking List Printed → Done.

## Reference data

Configuration menu:

| Record | Purpose |
|---|---|
| Facilities | Prescribing facilities. The name must match the OneDrive folder; the code matches the FHIR `Organization` ID. |
| Collection Points | Pickup points and e-lockers, with their district |
| Pickup Point Sync | Pulls collection points from Collect & Go (`GetLocation`). Also finds the CDU's own Collect & Go location. |
| Regimens | Each regimen code maps to drug components (products and daily dose), used to seed a prescription's product lines |

## Scheduled jobs

| Job | Runs | What it does |
|---|---|---|
| CDU OneDrive Poll | Every minute | Polls each enabled source whose own interval (default 15 minutes) is due |
| CDU Collect-and-Go Collection Point Sync | Every hour | Syncs each enabled configuration whose own interval (default 24 hours) is due |

The jobs tick often, but each source or configuration decides how often it actually runs.

## Tests

```sh
odoo -d <throwaway-db> -i cdu_prescription --without-demo=all --test-tags /cdu_prescription --stop-after-init --no-http
```

- `test_cdu_workflow.py` covers verification and validation, rejection history, role access and dashboards, and regimen product lines.
- `test_cdu_report_import.py` covers CSV and Excel imports, rows that match an existing prescription, and the error message for each bad row or file.
