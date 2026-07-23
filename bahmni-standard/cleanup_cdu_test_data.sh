#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
DATABASE_OVERRIDE=""
CONFIRMED=false

usage() {
    cat <<'EOF'
Usage:
  ./cleanup_cdu_test_data.sh [options]

Options:
  --confirm              Permanently delete CDU prescription test data.
                         Without this flag, the script only displays record counts.
  --env-file <path>      Compose environment file relative to bahmni-standard,
                         or an absolute path. Default: .env
  --database <name>      Override ODOO_DB_NAME from the environment file.
  -h, --help             Show this help message.

Examples:
  ./cleanup_cdu_test_data.sh
  ./cleanup_cdu_test_data.sh --confirm
  ./cleanup_cdu_test_data.sh --env-file .env.dev --confirm
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --confirm)
            CONFIRMED=true
            shift
            ;;
        --env-file)
            if [[ $# -lt 2 ]]; then
                echo "Error: --env-file requires a path." >&2
                usage >&2
                exit 2
            fi
            ENV_FILE="$2"
            shift 2
            ;;
        --database)
            if [[ $# -lt 2 ]]; then
                echo "Error: --database requires a database name." >&2
                usage >&2
                exit 2
            fi
            DATABASE_OVERRIDE="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Error: unknown option '$1'." >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ "${ENV_FILE}" != /* ]]; then
    ENV_FILE="${SCRIPT_DIR}/${ENV_FILE}"
fi

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "Error: environment file not found: ${ENV_FILE}" >&2
    exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
    echo "Error: Docker is not installed or is not available on PATH." >&2
    exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
    echo "Error: Docker Compose is not available." >&2
    exit 1
fi

# The Bahmni environment files are shell-compatible and are already sourced by
# other operational scripts in this directory.
set +u
set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a
set -u

ODOO_DB_HOST="${ODOO_DB_HOST:-odoodb}"
ODOO_DB_NAME="${DATABASE_OVERRIDE:-${ODOO_DB_NAME:-odoo}}"
ODOO_DB_USER="${ODOO_DB_USER:-odoo}"
ODOO_DB_PASSWORD="${ODOO_DB_PASSWORD:-odoo}"

cd "${SCRIPT_DIR}"
COMPOSE=(docker compose --env-file "${ENV_FILE}")

running_services="$("${COMPOSE[@]}" ps --status running --services)"
for required_service in odoo odoodb; do
    if ! grep -qx "${required_service}" <<<"${running_services}"; then
        echo "Error: required service '${required_service}' is not running." >&2
        echo "Start the Odoo profile before running this cleanup." >&2
        exit 1
    fi
done

print_counts() {
    "${COMPOSE[@]}" exec -T odoodb \
        psql -U "${ODOO_DB_USER}" -d "${ODOO_DB_NAME}" -P pager=off -c "
            SELECT 'Prescriptions' AS record_type, COUNT(*) FROM cdu_prescription
            UNION ALL
            SELECT 'Import runs', COUNT(*) FROM cdu_report_run
            UNION ALL
            SELECT 'Import rows', COUNT(*) FROM cdu_report_row
            UNION ALL
            SELECT 'Batches', COUNT(*) FROM cdu_batch
            UNION ALL
            SELECT 'Dispenses', COUNT(*) FROM cdu_dispense
            UNION ALL
            SELECT 'Bagging/QA records', COUNT(*) FROM cdu_bagging_qa
            UNION ALL
            SELECT 'Boxes', COUNT(*) FROM cdu_box
            UNION ALL
            SELECT 'Rejections', COUNT(*) FROM cdu_prescription_rejection
            ORDER BY 1;
        "
}

echo "CDU test-data cleanup"
echo "Database: ${ODOO_DB_NAME}"
echo
echo "Current transactional record counts:"
print_counts

if [[ "${CONFIRMED}" != true ]]; then
    echo
    echo "Preview only: no records were deleted."
    echo "Run './cleanup_cdu_test_data.sh --confirm' to perform the cleanup."
    exit 0
fi

cat <<'EOF'

WARNING: This permanently deletes all CDU prescriptions and their related:
  - report import history and rows
  - workload batches
  - dispensing records
  - bagging/QA records
  - boxes
  - rejection history

Patients, users, facilities, collection points, products, configuration, and
numbering sequences are preserved.
EOF

"${COMPOSE[@]}" exec -T odoo \
    odoo shell \
    -d "${ODOO_DB_NAME}" \
    --db_host "${ODOO_DB_HOST}" \
    --db_user "${ODOO_DB_USER}" \
    --db_password "${ODOO_DB_PASSWORD}" \
    --no-http \
    --log-level=error <<'PY'
cleanup_order = [
    "cdu.box",
    "cdu.bagging.qa",
    "cdu.dispense",
    "cdu.batch",
    "cdu.prescription",
    "cdu.report.run",
]

for model_name in cleanup_order:
    records = (
        env[model_name]
        .sudo()
        .with_context(active_test=False)
        .search([])
    )
    count = len(records)
    if records:
        records.unlink()
    print("Deleted %s record(s) from %s" % (count, model_name))

env.cr.commit()
print("CDU cleanup committed successfully.")
PY

remaining_records="$(
    "${COMPOSE[@]}" exec -T odoodb \
        psql -U "${ODOO_DB_USER}" -d "${ODOO_DB_NAME}" -tAc "
            SELECT
                (SELECT COUNT(*) FROM cdu_prescription)
              + (SELECT COUNT(*) FROM cdu_report_run)
              + (SELECT COUNT(*) FROM cdu_report_row)
              + (SELECT COUNT(*) FROM cdu_batch)
              + (SELECT COUNT(*) FROM cdu_dispense)
              + (SELECT COUNT(*) FROM cdu_bagging_qa)
              + (SELECT COUNT(*) FROM cdu_box)
              + (SELECT COUNT(*) FROM cdu_prescription_rejection);
        " | tr -d '[:space:]'
)"

echo
echo "Post-cleanup record counts:"
print_counts

if [[ "${remaining_records}" != "0" ]]; then
    echo "Error: cleanup verification found ${remaining_records} remaining transactional record(s)." >&2
    exit 1
fi

echo
echo "Cleanup complete. The CDU instance is ready for fresh report imports."
