# Bahmni Docker: Bonolo Health CDU

This fork of Bahmni Docker runs the Bonolo Health **Central Dispensing Unit (CDU)** in Odoo. The CDU:

1. receives ART prescriptions from eRegister;
2. verifies, validates and batches them;
3. picks, dispenses, bags, checks and boxes them;
4. hands the boxes to Collect & Go, which delivers them to pickup points and reports collections and returns.

The CDU's own code is in `bahmni-standard/extra-odoo-addons/` and `bahmni-standard/print-agent/`. Everything below [Running Bahmni](#running-bahmni) is the upstream Bahmni setup.

## Documentation

### In this repository

| Part | What it does | Read |
|---|---|---|
| `cdu_base` | CDU branding on the login page | Manifest only |
| `cdu_prescription` | Prescription intake (report files, OneDrive), verification, validation, rejections, workload batches, roles and menus | [README](bahmni-standard/extra-odoo-addons/cdu_prescription/README.md) |
| `cdu_elmis` | Picking, dispensing, bagging and QA, boxing and dispatch; eLMIS stock; Collect & Go submission and parcel tracking; the print queue | [README](bahmni-standard/extra-odoo-addons/cdu_elmis/README.md) |
| `cdu_eregister` | Pulls prescriptions from the FHIR repository and publishes the CDU's status back to eRegister | [README](bahmni-standard/extra-odoo-addons/cdu_eregister/README.md) |
| `cdu_reporting` | Operational and management reports | [README](bahmni-standard/extra-odoo-addons/cdu_reporting/README.md) |
| Print agent | Prints labels and box manifests from the Odoo print queue; runs on the CDU's Windows PCs | [README](bahmni-standard/print-agent/README.md) |

Install order follows the dependencies: `cdu_prescription` → `cdu_elmis` → `cdu_reporting`, and `cdu_eregister` on top of `cdu_prescription`.

### A prescription's states

```
cdu_prescription   awaiting verification → awaiting validation → awaiting batching
cdu_elmis          awaiting picking → awaiting dispensing → awaiting bagging / QA
                   → awaiting boxing → awaiting dispatch → dispatched
                   → collected, or returned uncollected (reported by Collect & Go)
```

Off the main path:
- **Rejected to call center** or **rejected to facility**, from verification, validation or dispensing. Once fixed, the prescription goes back to the stage it was rejected from.
- **Cancelled**, by a CDU admin.

`cdu_eregister` publishes every change to eRegister.

### Related repositories

| Repository | What it holds |
|---|---|
| [bonolo-cdu-fhir-ig](https://github.com/Bonolo-Health-CDU/bonolo-cdu-fhir-ig) | The FHIR contract between eRegister and the CDU: profiles, code lists and status values. Browse it at https://bonolo-health-cdu.github.io/bonolo-cdu-fhir-ig/ |
| [bonolo-health-eregister-automation](https://github.com/Bonolo-Health-CDU/bonolo-health-eregister-automation) | The prescription repository that sits between them (HAPI FHIR, OpenHIM, Keycloak, the portal) and the Postman tests |
| bahmni-docker (this one) | The CDU in Odoo and the print agent |

### Common tasks

Run these from `bahmni-standard`. `docker compose exec` skips the image's entrypoint, so pass the database connection yourself; the container already has it in `HOST`, `USER` and `PASSWORD`.

Upgrade a CDU module after pulling changes (the database is `ODOO_DB_NAME` in `.env`, `odoo` by default):

```shell
docker compose --env-file .env exec odoo sh -c \
  'odoo --db_host "$HOST" --db_user "$USER" --db_password "$PASSWORD" -d odoo -u cdu_elmis --stop-after-init --no-http'
docker compose --env-file .env restart odoo
```

Run a module's tests the same way, on a throwaway database, using the `odoo …` arguments from the module's README:

```shell
docker compose --env-file .env exec odoo sh -c \
  'odoo --db_host "$HOST" --db_user "$USER" --db_password "$PASSWORD" -d cdu_test -i cdu_elmis --without-demo=all --test-tags /cdu_elmis --stop-after-init --no-http'
```

## Running Bahmni

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
