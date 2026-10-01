"""Strings from the Bonolo CDU FHIR contract (bonolo-cdu-fhir-ig, NAMING.md).

FHIR compares these character for character: copy changes from NAMING.md,
never retype them.
"""

BASE = "http://fhir.health.gov.ls/bonolo-cdu"
SID = "http://fhir.health.gov.ls/sid"

# Identifier systems (NAMING.md section 3)
EREGISTER_ID_SYSTEM = SID + "/eregister-id"
NATIONAL_ID_SYSTEM = SID + "/national-id"
HIV_PROGRAM_ID_SYSTEM = SID + "/hiv-program-id"
FACILITY_CODE_SYSTEM = SID + "/dhis2-org-unit"
ORDER_UUID_SYSTEM = SID + "/eregister-order-uuid"
TASK_ID_SYSTEM = SID + "/cdu-fulfilment-task"
PICKUP_POINT_SYSTEM = SID + "/pickup-point-code"

# Record-ID rule for pickup points (NAMING.md section 5): "pup-" + code
PICKUP_POINT_ID_PREFIX = "pup-"

# Extensions on MedicationRequest
EXT_PICKUP_POINT = BASE + "/StructureDefinition/pickup-point"
EXT_REQUESTED_PICKUP_DATE = BASE + "/StructureDefinition/requested-pickup-date"
EXT_ORIGINATING_FACILITY = BASE + "/StructureDefinition/originating-facility"
EXT_NEXT_CLINICAL_VISIT_DATE = BASE + "/StructureDefinition/next-clinical-visit-date"

TASK_PROFILE = BASE + "/StructureDefinition/cdu-fulfilment-task"

FULFILMENT_STATUS_SYSTEM = BASE + "/CodeSystem/cdu-fulfilment-status"
REJECTION_REASON_SYSTEM = BASE + "/CodeSystem/cdu-rejection-reason"

NO_KNOWN_ALLERGY_CODES = {
    "716186003",  # SNOMED CT: No known allergy
    "409137002",  # SNOMED CT: No known drug allergy
}

FULFILMENT_STATUS_DISPLAY = {
    "received": "Received",
    "returned-to-facility": "Returned to facility",
    "query-raised": "Query raised",
    "in-preparation": "In preparation",
    "ready-for-dispatch": "Ready for dispatch",
    "dispatched": "Dispatched",
    "collected": "Collected",
    "uncollected-returned": "Returned uncollected",
    "cancelled": "Cancelled",
}

# CDU workflow state -> (Task.status, Task.businessStatus code).
# Source of truth: "Status Map" tab of Bonolo_CDU_eRegister_Data_Mapping.
STATE_TO_TASK_STATUS = {
    "awaiting_verification": ("accepted", "received"),
    "awaiting_validation": ("accepted", "received"),
    "rejected_to_call_center": ("on-hold", "query-raised"),
    "rejected_to_facility": ("rejected", "returned-to-facility"),
    "awaiting_batching": ("in-progress", "in-preparation"),
    "awaiting_picking": ("in-progress", "in-preparation"),
    "awaiting_dispensing": ("in-progress", "in-preparation"),
    "awaiting_bagging_qa": ("in-progress", "in-preparation"),
    "awaiting_boxing": ("in-progress", "in-preparation"),
    "awaiting_dispatch": ("in-progress", "ready-for-dispatch"),
    "dispatched": ("in-progress", "dispatched"),
    "cancelled": ("cancelled", "cancelled"),
}

# States the CDU has not started producing yet. An eRegister cancellation in
# one of these cancels the CDU prescription automatically; later states are
# flagged for an admin to decide.
PRE_PRODUCTION_STATES = (
    "awaiting_verification",
    "awaiting_validation",
    "rejected_to_call_center",
    "rejected_to_facility",
)

# cdu.rejection.reason code -> IG cdu-rejection-reason code
REJECTION_REASON_CODES = {
    "missing_patient_demographics": "missing-demographics",
    "missing_medicine_information": "missing-medicine",
    "missing_patient_clinical": "missing-clinical",
    "missing_collection_information": "missing-collection",
    "duplicate_prescription": "duplicate",
    "out_of_stock": "out-of-stock",
}
