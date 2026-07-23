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
