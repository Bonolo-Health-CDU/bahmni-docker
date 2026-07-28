#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB_NAME="${CDU_ODOO_DB:-odoo}"
DB_USER="${CDU_ODOO_DB_USER:-odoo}"
BACKUP_DIR="${CDU_RESET_BACKUP_DIR:-${SCRIPT_DIR}/backup-artifacts/cdu-prescription-reset}"
ASSUME_YES=false
CREATE_BACKUP=true

usage() {
    cat <<'EOF'
Usage: ./reset-cdu-prescriptions.sh [options]

Clear CDU patients, prescriptions, and prescription-workflow data from the
Odoo database. Staff users, facilities, collection points, products,
configuration, and eLMIS inventory are preserved.

Options:
  --yes              Skip the interactive RESET confirmation.
  --no-backup        Do not create a database backup before resetting.
  --database NAME    Odoo database name (default: odoo).
  --help              Show this help.

Environment overrides:
  CDU_ODOO_DB
  CDU_ODOO_DB_USER
  CDU_RESET_BACKUP_DIR
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes)
            ASSUME_YES=true
            shift
            ;;
        --no-backup)
            CREATE_BACKUP=false
            shift
            ;;
        --database)
            [[ $# -ge 2 ]] || {
                echo "Missing value for --database." >&2
                exit 2
            }
            DB_NAME="$2"
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ ! "$DB_NAME" =~ ^[A-Za-z0-9_]+$ ]]; then
    echo "Unsafe database name: $DB_NAME" >&2
    exit 2
fi

cd "$SCRIPT_DIR"

prescription_count="$(
    docker compose exec -T odoodb \
        psql -U "$DB_USER" -d "$DB_NAME" -Atc \
        "SELECT COUNT(*) FROM cdu_prescription;"
)"
patient_count="$(
    docker compose exec -T odoodb \
        psql -U "$DB_USER" -d "$DB_NAME" -Atc \
        "SELECT COUNT(*) FROM res_partner WHERE cdu_eregister_id IS NOT NULL;"
)"

echo "Database: $DB_NAME"
echo "Prescriptions to remove: $prescription_count"
echo "CDU patients to remove: $patient_count"
echo "This also clears CDU batches, import runs, dispensing, QA, and boxing workflow data."
echo "Staff users, facilities, products, configuration, and eLMIS inventory are preserved."

if [[ "$ASSUME_YES" != true ]]; then
    read -r -p "Type RESET to continue: " confirmation
    if [[ "$confirmation" != "RESET" ]]; then
        echo "Reset cancelled."
        exit 1
    fi
fi

if [[ "$CREATE_BACKUP" == true ]]; then
    mkdir -p "$BACKUP_DIR"
    timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
    backup_path="${BACKUP_DIR}/${DB_NAME}-before-prescription-reset-${timestamp}.dump"
    echo "Creating backup: $backup_path"
    docker compose exec -T odoodb \
        pg_dump -U "$DB_USER" -d "$DB_NAME" --format=custom > "$backup_path"
fi

docker compose exec -T odoodb \
    psql -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$DB_NAME" <<'SQL'
BEGIN;

CREATE TEMP TABLE cdu_reset_patient_ids ON COMMIT DROP AS
SELECT partner.id
FROM res_partner AS partner
WHERE partner.cdu_eregister_id IS NOT NULL
  AND partner.id NOT IN (SELECT partner_id FROM res_users)
  AND partner.id NOT IN (SELECT partner_id FROM res_company)
  AND partner.id NOT IN (
      SELECT partner_id
      FROM cdu_facility
      WHERE partner_id IS NOT NULL
  )
  AND partner.id NOT IN (
      SELECT partner_id
      FROM cdu_collection_point
      WHERE partner_id IS NOT NULL
  );

-- Remove polymorphic Odoo records first so old chatter cannot appear on a
-- future record that happens to reuse an identifier.
DELETE FROM mail_activity
WHERE res_model IN (
    'cdu.prescription',
    'cdu.batch',
    'cdu.dispense',
    'cdu.bagging.qa',
    'cdu.box',
    'cdu.report.run'
);

DELETE FROM mail_activity
WHERE res_model = 'res.partner'
  AND res_id IN (SELECT id FROM cdu_reset_patient_ids);

DELETE FROM mail_followers
WHERE res_model IN (
    'cdu.prescription',
    'cdu.batch',
    'cdu.dispense',
    'cdu.bagging.qa',
    'cdu.box',
    'cdu.report.run'
);

DELETE FROM mail_followers
WHERE res_model = 'res.partner'
  AND res_id IN (SELECT id FROM cdu_reset_patient_ids);

DELETE FROM mail_message
WHERE model IN (
    'cdu.prescription',
    'cdu.batch',
    'cdu.dispense',
    'cdu.bagging.qa',
    'cdu.box',
    'cdu.report.run'
);

DELETE FROM mail_message
WHERE model = 'res.partner'
  AND res_id IN (SELECT id FROM cdu_reset_patient_ids);

DELETE FROM ir_attachment
WHERE res_model IN (
    'cdu.prescription',
    'cdu.batch',
    'cdu.dispense',
    'cdu.bagging.qa',
    'cdu.box',
    'cdu.report.run'
);

DELETE FROM ir_attachment
WHERE res_model = 'res.partner'
  AND res_id IN (SELECT id FROM cdu_reset_patient_ids);

-- The cascade contains only prescription-dependent CDU workflow tables.
-- Identity sequences are deliberately retained to avoid reusing audit IDs.
TRUNCATE TABLE cdu_prescription CASCADE;

-- Empty workflow parents and import history remain after the prescription
-- cascade. Their foreign keys either cascade or become NULL as configured.
DELETE FROM cdu_box;
DELETE FROM cdu_batch;
DELETE FROM cdu_report_run;

-- Only contacts carrying a CDU eRegister identifier are patient records.
-- Explicit exclusions protect staff, company, facility, and collection-point
-- contacts even if such a contact is accidentally given an eRegister ID.
DELETE FROM res_partner
WHERE id IN (SELECT id FROM cdu_reset_patient_ids);

COMMIT;
SQL

remaining_count="$(
    docker compose exec -T odoodb \
        psql -U "$DB_USER" -d "$DB_NAME" -Atc \
        "SELECT COUNT(*) FROM cdu_prescription;"
)"
remaining_patient_count="$(
    docker compose exec -T odoodb \
        psql -U "$DB_USER" -d "$DB_NAME" -Atc \
        "SELECT COUNT(*) FROM res_partner WHERE cdu_eregister_id IS NOT NULL;"
)"

echo "Reset complete. Remaining prescriptions: $remaining_count"
echo "Remaining CDU patients: $remaining_patient_count"
if [[ "$CREATE_BACKUP" == true ]]; then
    echo "Backup: $backup_path"
fi
