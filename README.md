# Bahmni Docker

Refer to the official wiki for full documentation:
[Running Bahmni on Docker](https://bahmni.atlassian.net/wiki/spaces/BAH/pages/299630726/Running+Bahmni+on+Docker)

## Run Bahmni (Lite or Standard)

1. Go to the relevant folder:
```shell
cd bahmni-lite
# or
cd bahmni-standard
```

2. Ensure the selected `.env` file has the expected profile (`COMPOSE_PROFILES`).

3. Start services using either script or docker compose:
```shell
./run-bahmni.sh
```
```shell
docker compose --env-file .env up -d
```

## Environment Files

- `.env` typically points to stable/tested tags.
- `.env.dev` typically points to `latest` tags for development/testing.
- You can also use a custom file (for example `.env.local`).

Examples:
```shell
./run-bahmni.sh
./run-bahmni.sh .env.dev
./run-bahmni.sh .env.local
```

## Odoo-Only Instance (bahmni-standard)

If you want an Odoo-only setup, set this in `bahmni-standard/.env`:
```shell
COMPOSE_PROFILES=odoo
```

Then start:
```shell
cd bahmni-standard
mkdir -p extra-odoo-addons
docker compose --env-file .env up -d
```

Check status:
```shell
docker compose --env-file .env ps
```

Verify Odoo endpoint:
```shell
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8069/web/login
```
Expected response: `200`.

## CDU Test Data Workflow

The `bahmni-standard` deployment includes three fictional eRegister reports for
repeatable CDU workflow testing:

- `test-data/cdu/fictitious_eregister_likotsi_filter_clinic_50_prescriptions.csv`
- `test-data/cdu/fictitious_eregister_qoaling_filter_clinic_50_prescriptions.csv`
- `test-data/cdu/fictitious_eregister_mafeteng_hospital_50_prescriptions.csv`

Each report contains 50 prescriptions. All patient names, identifiers, contacts,
addresses, and clinical values are synthetic and clearly marked as fictitious.
These files must only be used in test or demonstration environments.

### Prerequisites

Start Odoo and confirm that the application and database services are running:

```shell
cd bahmni-standard
docker compose --env-file .env up -d
docker compose --env-file .env ps odoo odoodb
```

The report importer only accepts CSV files. Do not convert the supplied reports
to XLSX before uploading them.

The included reports use valid Collect-and-Go pickup-point names. If the
environment has not yet synchronized those locations, sign in as a CDU
administrator and run **Configuration → Pickup Point Sync** before importing.

### Import All Three Reports

The bulk importer is the quickest option:

1. Sign in as a CDU Data Clerk or CDU Administrator.
2. Open **Central Dispensing Unit → Prescriptions → Intake → Import Multiple Reports**.
3. Upload all three CSV files from `bahmni-standard/test-data/cdu/`.
4. Click **Import Reports**.
5. Review the result cards and individual file results.

On a clean CDU dataset, the expected result is:

- 3 completed files
- 150 imported prescriptions
- 0 failed rows
- 0 duplicate rows

To test one report at a time:

1. Open **Central Dispensing Unit → Prescriptions → Intake → Report Imports**.
2. Click **New**.
3. Upload one CSV in the **Report File** field.
4. Leave **Source Facility** empty so the importer uses the file's `Location`
   column.
5. Click **Import Report** and confirm that the state becomes **Completed**.

### Remove CDU Transactional Test Data

`bahmni-standard/cleanup_cdu_test_data.sh` removes CDU prescriptions and all
dependent transactional records in a safe dependency order. It clears:

- report import history and rows
- prescriptions and rejection history
- workload batches
- dispensing and bagging/QA records
- boxes

It preserves patients, users, facilities, collection points, products,
configuration, and all numbering sequences.

The script is read-only unless `--confirm` is supplied. Preview the affected
record counts:

```shell
cd bahmni-standard
./cleanup_cdu_test_data.sh
```

Perform the cleanup:

```shell
./cleanup_cdu_test_data.sh --confirm
```

Use a different Compose environment file when required:

```shell
./cleanup_cdu_test_data.sh --env-file .env.dev --confirm
```

After cleanup, the script verifies that every CDU transactional table is empty.
You can then upload the same fictitious reports again as a fresh prescription
import.

> **Warning:** Cleanup is permanent. Run it only against a disposable test or
> demonstration database.

## Troubleshooting: Odoo Permission Error on Filestore

### Symptom

Odoo container is running but UI is not accessible, and logs show:
- `PermissionError: [Errno 13] Permission denied`
- path under `/var/lib/odoo/filestore/...`

### Cause

The shared filestore volume is owned by `root:root`, while Odoo runs as `odoo` (`uid=101`).

### Fix

From `bahmni-standard`:
```shell
docker compose --env-file .env logs --tail=120 odoo
docker compose --env-file .env exec odoo id
docker compose --env-file .env exec odoo ls -ld /var/lib/odoo/filestore /var/lib/odoo/filestore/odoo
docker compose --env-file .env exec --user root odoo chown -R odoo:odoo /var/lib/odoo/filestore
docker compose --env-file .env restart odoo
docker compose --env-file .env ps
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8069/web/login
```

Expected response after fix: `200`.
