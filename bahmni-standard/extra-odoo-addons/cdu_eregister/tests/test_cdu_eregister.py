import copy
import json
from datetime import date, timedelta
from unittest.mock import patch
from urllib.parse import urlsplit

from odoo.tests.common import TransactionCase, tagged

from ..models import fhir_contract as fc


BASE_URL = "http://fhir.test/fhir"
CDU_ORG = "1-LESOTHO-CDU"
ORDER_UUID = "6f1c2c5e-0000-4000-8000-000000000001"


class FakeFhirServer:
    """Just enough of the repository behind OpenHIM for the client's calls."""

    def __init__(self):
        self.resources = {}
        self.versions = {}
        self.calls = []
        self.put_failure = None

    def add(self, resource):
        reference = "%s/%s" % (resource["resourceType"], resource["id"])
        self.versions[reference] = self.versions.get(reference, 0) + 1
        resource = copy.deepcopy(resource)
        resource["meta"] = dict(resource.get("meta") or {}, versionId=str(self.versions[reference]))
        self.resources[reference] = resource
        return resource

    def get_resource(self, reference):
        return self.resources[reference]

    def update(self, reference, **changes):
        resource = copy.deepcopy(self.resources[reference])
        resource.update(changes)
        return self.add(resource)

    def handle(self, method, url, params=None, body=None, headers=None):
        path = urlsplit(url).path[len(urlsplit(BASE_URL).path):].strip("/")
        self.calls.append({"method": method, "path": path, "params": params, "body": body, "headers": headers})
        if method == "PUT":
            return self._put(path, body, headers)
        if path == "metadata":
            return 200, json.dumps({"resourceType": "CapabilityStatement", "fhirVersion": "4.0.1"})
        if path == "Task":
            return 200, json.dumps(self._search_tasks(params or {}))
        if path == "AllergyIntolerance":
            patient = (params or {}).get("patient")
            entries = [
                {"resource": resource, "search": {"mode": "match"}}
                for resource in self.resources.values()
                if resource["resourceType"] == "AllergyIntolerance"
                and resource["patient"]["reference"] == patient
            ]
            return 200, json.dumps({"resourceType": "Bundle", "type": "searchset", "entry": entries})
        if path in self.resources:
            return 200, json.dumps(self.resources[path])
        return 404, json.dumps(
            {"resourceType": "OperationOutcome", "issue": [{"severity": "error", "diagnostics": "Not found: %s" % path}]}
        )

    def _search_tasks(self, params):
        matches = [
            resource
            for resource in self.resources.values()
            if resource["resourceType"] == "Task"
            and resource["status"] == params.get("status")
            and resource["owner"]["reference"] == params.get("owner")
        ]
        entries = [{"resource": task, "search": {"mode": "match"}} for task in matches]
        for task in matches:
            if "Task:focus" in (params.get("_include") or []):
                entries.append({"resource": self.resources[task["focus"]["reference"]], "search": {"mode": "include"}})
            if "Task:patient" in (params.get("_include") or []):
                entries.append({"resource": self.resources[task["for"]["reference"]], "search": {"mode": "include"}})
        return {"resourceType": "Bundle", "type": "searchset", "total": len(matches), "entry": entries}

    def _put(self, path, body, headers):
        if self.put_failure:
            return self.put_failure
        current = self.resources[path]
        expected = 'W/"%s"' % current["meta"]["versionId"]
        if (headers or {}).get("If-Match") != expected:
            return 409, json.dumps(
                {"resourceType": "OperationOutcome", "issue": [{"severity": "error", "diagnostics": "Version conflict"}]}
            )
        return 200, json.dumps(self.add(body))


@tagged("post_install", "-at_install", "cdu_eregister")
class TestCduEregisterIntegration(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        params = cls.env["ir.config_parameter"].sudo()
        params.set_param("cdu.eregister.base_url", BASE_URL)
        params.set_param("cdu.eregister.username", "cdu")
        params.set_param("cdu.eregister.password", "secret")
        params.set_param("cdu.eregister.cdu_organization_id", CDU_ORG)
        params.set_param("cdu.eregister.sync_enabled", "True")
        params.set_param("cdu.eregister.max_publish_attempts", "3")
        params.set_param("cdu.eregister.cancellation_watermark", False)

        cls.data_clerk = cls.env.ref("cdu_prescription.user_cdu_data_clerk")
        cls.collection_point = cls.env["cdu.collection.point"].create(
            {
                "name": "Maseru Mall Collect&Go",
                "code": "TEST-501-MASERU-MALL",
                "remote_location_id": "test-501",
            }
        )
        cls.pickup_date = date.today() + timedelta(days=7)
        cls.visit_date = date.today() + timedelta(days=90)

    def setUp(self):
        super().setUp()
        self.server = FakeFhirServer()
        self._publish_prescription()
        server = self.server
        patcher = patch.object(
            type(self.env["cdu.eregister.client"]),
            "_send",
            lambda _self, method, url, params=None, body=None, headers=None: server.handle(
                method, url, params=params, body=body, headers=headers
            ),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sync = self.env["cdu.eregister.sync"]

    # ------------------------------------------------------------------
    # Fixtures: what eRegister's transaction Bundle leaves in the repository
    # ------------------------------------------------------------------
    def _publish_prescription(self):
        self.server.add(
            {
                "resourceType": "Organization",
                "id": "TEST-A2681",
                "identifier": [{"system": fc.FACILITY_CODE_SYSTEM, "value": "TEST-A2681"}],
                "name": "Maseru Regional Hospital",
            }
        )
        self.server.add(
            {
                "resourceType": "Location",
                "id": "pup-test-501",
                "identifier": [{"system": fc.PICKUP_POINT_SYSTEM, "value": "test-501"}],
                "name": "Maseru Mall Collect&Go",
            }
        )
        self.server.add(
            {
                "resourceType": "Patient",
                "id": "10",
                "identifier": [
                    {"system": fc.EREGISTER_ID_SYSTEM, "value": "TEST-E0000000001/26"},
                    {"system": fc.HIV_PROGRAM_ID_SYSTEM, "value": "TEST-HIV-1234"},
                ],
                "name": [{"use": "official", "family": "Mokoena", "given": ["Palesa"]}],
                "gender": "female",
                "birthDate": "1990-05-14",
                "telecom": [
                    {"system": "phone", "value": "+26650000002", "rank": 2},
                    {"system": "phone", "value": "+26650000001", "rank": 1},
                ],
                "address": [{"district": "Maseru", "country": "LS"}],
            }
        )
        self.server.add(
            {
                "resourceType": "AllergyIntolerance",
                "id": "11",
                "clinicalStatus": {"coding": [{"code": "active"}]},
                "code": {"coding": [{"system": "http://snomed.info/sct", "code": "716186003"}]},
                "patient": {"reference": "Patient/10"},
            }
        )
        self.server.add(
            {
                "resourceType": "MedicationRequest",
                "id": "12",
                "extension": [
                    {"url": fc.EXT_ORIGINATING_FACILITY, "valueReference": {"reference": "Organization/TEST-A2681"}},
                    {"url": fc.EXT_PICKUP_POINT, "valueReference": {"reference": "Location/pup-test-501"}},
                    {"url": fc.EXT_REQUESTED_PICKUP_DATE, "valueDate": self.pickup_date.isoformat()},
                    {"url": fc.EXT_NEXT_CLINICAL_VISIT_DATE, "valueDate": self.visit_date.isoformat()},
                ],
                "identifier": [{"system": fc.ORDER_UUID_SYSTEM, "value": ORDER_UUID}],
                "status": "active",
                "intent": "order",
                "medicationCodeableConcept": {
                    "coding": [{"system": fc.BASE + "/CodeSystem/art-regimen", "code": "1j", "display": "TDF-3TC-DTG"}]
                },
                "subject": {"reference": "Patient/10"},
                "authoredOn": "2026-10-01T09:30:00+02:00",
                "requester": {"display": "Dr M. Lerotholi"},
                "dosageInstruction": [{"patientInstruction": "Take one tablet daily"}],
            }
        )
        self.server.add(
            {
                "resourceType": "Task",
                "id": "13",
                "meta": {"profile": [fc.TASK_PROFILE]},
                "identifier": [{"system": fc.TASK_ID_SYSTEM, "value": ORDER_UUID}],
                "status": "requested",
                "intent": "order",
                "code": {"coding": [{"system": "http://hl7.org/fhir/CodeSystem/task-code", "code": "fulfill"}]},
                "focus": {"reference": "MedicationRequest/12"},
                "for": {"reference": "Patient/10"},
                "authoredOn": "2026-10-01T09:30:00+02:00",
                "lastModified": "2026-10-01T09:30:00+02:00",
                "requester": {"reference": "Organization/TEST-A2681"},
                "owner": {"reference": "Organization/%s" % CDU_ORG},
            }
        )

    def _pull(self):
        self.sync.pull_new_prescriptions()
        return self.env["cdu.prescription"].search([("eregister_order_uuid", "=", ORDER_UUID)])

    def _task(self):
        return self.server.get_resource("Task/13")

    def _puts(self):
        return [call for call in self.server.calls if call["method"] == "PUT"]

    # ------------------------------------------------------------------
    # INT-02: intake
    # ------------------------------------------------------------------
    def test_pull_creates_prescription_and_claims_task(self):
        prescription = self._pull()

        self.assertEqual(len(prescription), 1)
        self.assertEqual(prescription.intake_source, "eregister_fhir")
        self.assertEqual(prescription.state, "awaiting_verification")
        self.assertEqual(prescription.patient_first_name, "Palesa Mokoena")
        self.assertEqual(prescription.patient_identifier, "TEST-E0000000001/26")
        self.assertEqual(prescription.hiv_program_id, "TEST-HIV-1234")
        self.assertEqual(prescription.patient_gender, "female")
        self.assertEqual(prescription.patient_phone, "+26650000001")
        self.assertEqual(prescription.secondary_contact, "+26650000002")
        self.assertEqual(prescription.regimen_prescribed_raw, "1j = TDF-3TC-DTG")
        self.assertEqual(prescription.dosage_instructions, "Take one tablet daily")
        self.assertEqual(prescription.prescription_date, date(2026, 10, 1))
        self.assertEqual(prescription.next_drug_pickup_date, self.pickup_date)
        self.assertEqual(prescription.next_clinical_visit_date, self.visit_date)
        self.assertEqual(prescription.collection_point_id, self.collection_point)
        self.assertEqual(prescription.facility_id.code, "TEST-A2681")
        self.assertEqual(prescription.facility_id.name, "Maseru Regional Hospital")
        self.assertEqual(prescription.prescriber_name, "Dr M. Lerotholi")
        self.assertEqual(prescription.has_allergies, "no")
        self.assertEqual(prescription.patient_id.cdu_eregister_id, "TEST-E0000000001/26")

        task = self._task()
        self.assertEqual(task["status"], "accepted")
        self.assertEqual(task["businessStatus"]["coding"][0]["code"], "received")
        self.assertEqual(task["meta"]["profile"], [fc.TASK_PROFILE])
        self.assertEqual(self._puts()[0]["headers"]["If-Match"], 'W/"1"')
        self.assertEqual(prescription.fhir_sync_state, "synced")
        self.assertEqual(prescription.fhir_business_status, "received")

        ingest_log = self.env["cdu.eregister.api.log"].search([("call_type", "=", "INGEST")])
        self.assertEqual(len(ingest_log), 1)
        self.assertTrue(ingest_log.success)
        self.assertEqual(ingest_log.http_method, "GET")
        self.assertEqual(ingest_log.http_status_code, 200)
        self.assertTrue(ingest_log.endpoint.startswith(BASE_URL + "/Task "))
        self.assertIn('"status": "requested"', ingest_log.endpoint)
        self.assertEqual(ingest_log.fhir_reference, "Task/13")
        self.assertEqual(ingest_log.prescription_id, prescription)
        self.assertEqual(json.loads(ingest_log.response_body)["id"], "13")

    def test_resent_task_does_not_duplicate(self):
        first = self._pull()
        self.server.update("Task/13", status="requested")

        second = self._pull()

        self.assertEqual(first, second)
        self.assertEqual(self.env["cdu.prescription"].search_count([("eregister_order_uuid", "=", ORDER_UUID)]), 1)
        self.assertEqual(self._task()["status"], "accepted")

    def test_failed_intake_is_logged_and_does_not_claim(self):
        medication_request = self.server.get_resource("MedicationRequest/12")
        self.server.update(
            "MedicationRequest/12",
            extension=[
                extension
                for extension in medication_request["extension"]
                if extension["url"] != fc.EXT_REQUESTED_PICKUP_DATE
            ],
        )

        prescription = self._pull()

        self.assertFalse(prescription)
        self.assertEqual(self._task()["status"], "requested")
        log = self.env["cdu.eregister.api.log"].search([("fhir_reference", "=", "Task/13")], limit=1)
        self.assertFalse(log.success)
        self.assertEqual(log.http_method, "GET")
        self.assertEqual(log.http_status_code, 200)
        self.assertIn("Next Drug Pickup Date", log.error_message)

    # ------------------------------------------------------------------
    # INT-03: status publication
    # ------------------------------------------------------------------
    def test_state_change_publishes_mapped_status(self):
        prescription = self._pull()

        prescription.write({"state": "awaiting_batching"})
        self.assertEqual(prescription.fhir_sync_state, "pending")
        self.sync.cron_push_pending_statuses()

        task = self._task()
        self.assertEqual(task["status"], "in-progress")
        self.assertEqual(task["businessStatus"]["coding"][0]["code"], "in-preparation")
        self.assertEqual(prescription.fhir_sync_state, "synced")

    def test_same_external_status_is_not_republished(self):
        prescription = self._pull()
        put_count = len(self._puts())

        prescription.write({"state": "awaiting_validation"})

        self.assertEqual(prescription.fhir_sync_state, "synced")
        self.sync.cron_push_pending_statuses()
        self.assertEqual(len(self._puts()), put_count)

    def test_rejection_to_facility_publishes_reason(self):
        prescription = self._pull()

        prescription.with_user(self.data_clerk)._perform_rejection(
            "facility", ["missing_patient_demographics", "missing_collection_information"]
        )
        self.sync.cron_push_pending_statuses()

        task = self._task()
        self.assertEqual(task["status"], "rejected")
        self.assertEqual(task["businessStatus"]["coding"][0]["code"], "returned-to-facility")
        self.assertEqual(task["statusReason"]["coding"][0]["code"], "missing-demographics")
        self.assertIn("Missing Medicine Collection Information", task["statusReason"]["text"])

    def test_facility_resubmission_returns_to_previous_stage(self):
        prescription = self._pull()
        prescription.with_user(self.data_clerk)._perform_rejection("facility", ["missing_patient_demographics"])
        self.sync.cron_push_pending_statuses()

        # The facility corrects the prescription and puts the Task back in the queue.
        self.server.update("MedicationRequest/12", dosageInstruction=[{"patientInstruction": "Take one tablet at night"}])
        self.server.update("Task/13", status="requested")
        self._pull()

        self.assertEqual(prescription.state, "awaiting_verification")
        self.assertEqual(prescription.dosage_instructions, "Take one tablet at night")
        self.assertFalse(prescription.active_rejection_id)
        self.assertFalse(prescription.rejection_history_ids.is_active)
        task = self._task()
        self.assertEqual(task["status"], "accepted")
        self.assertEqual(task["businessStatus"]["coding"][0]["code"], "received")
        self.assertNotIn("statusReason", task)

    def test_failed_publication_retries_then_gives_up(self):
        prescription = self._pull()
        self.server.put_failure = (
            412,
            json.dumps(
                {
                    "resourceType": "OperationOutcome",
                    "issue": [{"severity": "error", "diagnostics": "Task.businessStatus: code not in value set"}],
                }
            ),
        )

        prescription.write({"state": "awaiting_batching"})
        self.sync.cron_push_pending_statuses()
        self.assertEqual(prescription.fhir_sync_state, "pending")
        self.assertIn("code not in value set", prescription.fhir_sync_error)

        self.sync.cron_push_pending_statuses()
        self.sync.cron_push_pending_statuses()
        self.assertEqual(prescription.fhir_sync_state, "failed")
        self.assertEqual(prescription.fhir_sync_attempts, 3)

    def test_query_raised_keeps_task_on_hold(self):
        prescription = self._pull()

        prescription.with_user(self.data_clerk)._perform_rejection("call_center", ["missing_patient_clinical"])
        self.sync.cron_push_pending_statuses()

        task = self._task()
        self.assertEqual(task["status"], "on-hold")
        self.assertEqual(task["businessStatus"]["coding"][0]["code"], "query-raised")

    def test_cdu_cancellation_is_published_and_not_mistaken_for_eregister(self):
        prescription = self._pull()

        prescription.write({"state": "cancelled"})
        self.sync.cron_push_pending_statuses()

        task = self._task()
        self.assertEqual(task["status"], "cancelled")
        self.assertEqual(task["businessStatus"]["coding"][0]["code"], "cancelled")
        self.assertEqual(prescription.fhir_sync_state, "synced")

        # The cancelled Task now shows up in the cancellation search.
        self.assertEqual(self.sync.check_cancellations(), 0)
        self.assertFalse(prescription.eregister_cancelled)
        self.assertFalse(prescription.activity_ids)

        # Re-publishing the same CDU cancellation is a no-op, not an eRegister event.
        put_count = len(self._puts())
        prescription.write({"fhir_sync_state": "pending"})
        ok, _message = self.sync.push_prescription_status(prescription)
        self.assertTrue(ok)
        self.assertFalse(prescription.eregister_cancelled)
        self.assertEqual(len(self._puts()), put_count)

    # ------------------------------------------------------------------
    # eRegister cancellations
    # ------------------------------------------------------------------
    def test_cancellation_before_production_cancels_prescription(self):
        prescription = self._pull()
        self.server.update("Task/13", status="cancelled")

        self.assertEqual(self.sync.check_cancellations(), 1)

        self.assertEqual(prescription.state, "cancelled")
        self.assertTrue(prescription.eregister_cancelled)
        self.assertEqual(prescription.fhir_sync_state, "not_applicable")
        self.assertEqual(self._task()["status"], "cancelled")
        log = self.env["cdu.eregister.api.log"].search([("call_type", "=", "CHECK_CANCELLATIONS")])
        self.assertEqual((log.http_method, log.http_status_code), ("GET", 200))
        self.assertIn('"status": "cancelled"', log.endpoint)

    def test_cancellation_during_production_is_flagged_not_cancelled(self):
        prescription = self._pull()
        prescription.write({"state": "awaiting_dispensing"})
        self.sync.cron_push_pending_statuses()
        self.server.update("Task/13", status="cancelled")

        self.sync.check_cancellations()

        self.assertEqual(prescription.state, "awaiting_dispensing")
        self.assertTrue(prescription.eregister_cancelled)
        self.assertTrue(prescription.activity_ids)
        # Later CDU progress is no longer published over eRegister's cancellation.
        put_count = len(self._puts())
        prescription.write({"state": "awaiting_bagging_qa"})
        self.sync.cron_push_pending_statuses()
        self.assertEqual(len(self._puts()), put_count)

    def test_publication_never_overwrites_a_cancelled_task(self):
        prescription = self._pull()
        self.server.update("Task/13", status="cancelled")

        prescription.with_user(self.data_clerk)._perform_rejection("call_center", ["missing_patient_clinical"])
        self.assertEqual(prescription.fhir_sync_state, "pending")
        self.sync.cron_push_pending_statuses()

        self.assertEqual(self._task()["status"], "cancelled")
        self.assertEqual(prescription.state, "cancelled")
        self.assertTrue(prescription.eregister_cancelled)
        log = self.env["cdu.eregister.api.log"].search([("call_type", "=", "CHECK_CANCELLATIONS")])
        self.assertEqual((log.http_method, log.http_status_code), ("GET", 200))
        self.assertEqual(log.endpoint, BASE_URL + "/Task/13")

    # ------------------------------------------------------------------
    # Client
    # ------------------------------------------------------------------
    def test_paging_links_are_rerooted_on_the_configured_base(self):
        client = self.env["cdu.eregister.client"]
        self.assertEqual(
            client._url("http://localhost:5001/fhir?_getpages=abc&_getpagesoffset=50"),
            BASE_URL + "?_getpages=abc&_getpagesoffset=50",
        )
        self.assertEqual(client._url("Task/13"), BASE_URL + "/Task/13")
