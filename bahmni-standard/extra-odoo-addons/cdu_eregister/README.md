# CDU eRegister Integration Client

The Odoo side of the eRegister ↔ CDU exchange. It talks to the FHIR prescription repository only through OpenHIM, as the `cdu` client.

| Flow | What happens | Schedule |
|---|---|---|
| INT-02 pull | Searches `Task?owner=Organization/<CDU>&status=requested` with the MedicationRequest and Patient included. Creates a `cdu.prescription` (intake source *eRegister (FHIR)*), matching facility and collection point. | every 2 min* |
| INT-03 status | Every CDU state change is published on the Task (`status`, `businessStatus`, `statusReason`). Read first, then written back with `If-Match`, so an eRegister change made in between is never overwritten. | every 1 min* |
| Cancellations | Searches cancelled Tasks owned by the CDU. Before production (verification, validation, rejected) the CDU prescription is cancelled automatically. Later it is flagged with a banner and an activity for an admin. | every 5 min* |

\* Defaults. Change them in CDU › Configuration › eRegister Integration › Synchronisation (1 minute to 24 hours). They are stored on the scheduled jobs themselves, which module upgrades don't overwrite.

Claiming a Task is just the first status publication (`accepted` / `received`). The prescription is committed before the claim is sent, so a claimed Task is never lost.

## Status mapping

| CDU state | Task.status | businessStatus |
|---|---|---|
| awaiting verification / validation | accepted | received |
| rejected to call center | on-hold | query-raised (+ statusReason) |
| rejected to facility | rejected | returned-to-facility (+ statusReason) |
| awaiting batching … boxing | in-progress | in-preparation |
| awaiting dispatch | in-progress | ready-for-dispatch |
| dispatched | in-progress | dispatched |
| cancelled by the CDU | cancelled | cancelled |

This follows the "Status Map" tab of the Bonolo_CDU_eRegister_Data_Mapping sheet. eRegister also cancels with `cancelled` / `cancelled`; the module tells its own cancellation apart by what it last published, so a CDU cancellation is never read back as an eRegister one.

`collected` and `uncollected-returned` need Collect & Go parcel events and are not published yet. R4 `Task.statusReason` holds one concept: it carries the first rejection reason as a code and all reasons in its text.

## Resubmission

When the facility corrects a returned prescription, eRegister sets the Task back to `requested`. The next pull updates the CDU prescription from the amended MedicationRequest, reseeds regimen products if the regimen changed, and returns it to the stage it was rejected from.

## Matching reference data

- Facility: `Organization/<id>`, where the id is the facility code (NAMING.md §5). An unknown facility is created from the Organization record.
- Pickup point: `Location/pup-<ref>` → `cdu.collection.point.remote_location_id = <ref>`. If that ref is shared by several collection points, the Location name decides. If nothing matches, the name is kept in *Drug Pickup Point* for the clerk to resolve.

## EMR Sync button

CDU staff see an **EMR Sync** button in the top bar, next to their name. The coloured dot shows sync health:

| Dot | Meaning |
|---|---|
| Green | Up to date |
| Amber | Never synced, or no successful sync for two pull intervals (at least 10 minutes) while automatic sync is on |
| Red | The last attempt failed |
| Grey | Automatic sync off, or not configured |

Clicking it shows:
- the last successful sync, what it pulled (new, re-sent, failed) and whether it was automatic or started by someone
- the last new prescription received
- the error from a failed attempt
- status updates that could not be sent to eRegister
- **Pull prescriptions now**

Administrators also get links to the settings and the API log.

Every pull is recorded in `ir.config_parameter` (`cdu.eregister.last_pull`, `cdu.eregister.last_successful_pull`), whether it was scheduled, started from this button or started from Settings. A Postgres advisory lock lets only one pull run at a time, so a manual pull and the scheduled one never overlap.

## Configuration

CDU › Configuration › eRegister Integration. The OpenHIM password is not shipped with the module; synchronisation stays idle until it is set. Use **Test Connection** and **Pull Prescriptions Now** to check the setup. Every write, and every failed call, is in CDU › Configuration › eRegister API Logs.

## Tests

```sh
odoo -d <throwaway-db> -i cdu_eregister --without-demo=all --test-tags /cdu_eregister --stop-after-init --no-http
```

The tests run against an in-memory fake of the repository; they make no network calls.
