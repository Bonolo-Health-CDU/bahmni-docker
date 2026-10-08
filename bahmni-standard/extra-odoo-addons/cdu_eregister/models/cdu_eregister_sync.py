import copy
import logging
import threading
from datetime import timedelta, timezone

from odoo import _, fields, models
from odoo.exceptions import UserError

from .fhir_contract import (
    EREGISTER_ID_SYSTEM,
    EXT_NEXT_CLINICAL_VISIT_DATE,
    EXT_ORIGINATING_FACILITY,
    EXT_PICKUP_POINT,
    EXT_REQUESTED_PICKUP_DATE,
    FACILITY_CODE_SYSTEM,
    FULFILMENT_STATUS_DISPLAY,
    FULFILMENT_STATUS_SYSTEM,
    HIV_PROGRAM_ID_SYSTEM,
    NATIONAL_ID_SYSTEM,
    NO_KNOWN_ALLERGY_CODES,
    ORDER_UUID_SYSTEM,
    PICKUP_POINT_ID_PREFIX,
    PRE_PRODUCTION_STATES,
    REJECTION_REASON_SYSTEM,
    TASK_ID_SYSTEM,
)


_logger = logging.getLogger(__name__)

# Lets a cancellation search overlap the previous run, so a Task updated while
# the previous run was in flight is not missed. Processing is idempotent.
CANCELLATION_OVERLAP = timedelta(minutes=10)


class CduEregisterSync(models.AbstractModel):
    """INT-02 (pull prescriptions) and INT-03 (publish status) for the CDU."""

    _name = "cdu.eregister.sync"
    _description = "CDU eRegister Synchronisation"

    # ------------------------------------------------------------------
    # Scheduled entry points
    # ------------------------------------------------------------------
    def _sync_enabled(self):
        client = self.env["cdu.eregister.client"]
        return client._param("cdu.eregister.sync_enabled") == "True" and client.is_configured()

    def cron_pull_new_prescriptions(self):
        if self._sync_enabled():
            self.pull_new_prescriptions(commit=True)

    def cron_push_pending_statuses(self):
        if not self._sync_enabled():
            return
        prescriptions = self.env["cdu.prescription"].sudo().search(
            [("fhir_sync_state", "=", "pending")],
            order="write_date asc, id asc",
            limit=100,
        )
        for prescription in prescriptions:
            self.push_prescription_status(prescription)
            self._commit(True)

    def cron_check_cancellations(self):
        if self._sync_enabled():
            self.check_cancellations(commit=True)

    def _commit(self, commit):
        # Commit after each prescription so a claimed Task is never lost to a
        # later failure in the same run. Never commit inside tests.
        testing = getattr(threading.current_thread(), "testing", False) or self.env.registry.in_test_mode()
        if commit and not testing:
            self.env.cr.commit()

    # ------------------------------------------------------------------
    # INT-02: pull new prescriptions
    # ------------------------------------------------------------------
    def pull_new_prescriptions(self, commit=False):
        client = self.env["cdu.eregister.client"]
        params = {
            "owner": "Organization/%s" % client._cdu_organization_id(),
            "status": "requested",
            "_include": ["Task:focus", "Task:patient"],
            "_sort": "authored-on",
            "_count": client._page_size(),
        }
        index, matches, sources = client.search_all("Task", params, "PULL_TASKS")
        tasks = [resource for resource in matches if resource.get("resourceType") == "Task"]

        summary = {"created": 0, "updated": 0, "failed": 0}
        cache = {}
        for task in tasks:
            task_reference = "Task/%s" % task.get("id")
            try:
                with self.env.cr.savepoint():
                    prescription, outcome = self._ingest_task(task, index, cache)
            except Exception as exc:  # one bad prescription must not block the queue
                _logger.warning("eRegister intake of %s failed: %s", task_reference, exc)
                summary["failed"] += 1
                client.log_resource_outcome("INGEST", sources[task_reference], task, False, str(exc))
                self._commit(commit)
                continue
            summary[outcome] += 1
            client.log_resource_outcome("INGEST", sources[task_reference], task, True, prescription=prescription)
            self._commit(commit)
            self.push_prescription_status(prescription)
            self._commit(commit)
        return summary

    def _ingest_task(self, task, index, cache):
        medication_request = self._resolve(task.get("focus"), index, "MedicationRequest")
        patient = self._resolve(task.get("for"), index, "Patient")

        order_uuid = self._identifier(medication_request, ORDER_UUID_SYSTEM) or self._identifier(
            task, TASK_ID_SYSTEM
        )
        if not order_uuid:
            raise UserError(_("The prescription has no eRegister order UUID."))
        if medication_request.get("status") in ("cancelled", "stopped", "entered-in-error"):
            raise UserError(
                _("MedicationRequest/%(id)s is %(status)s but its Task is still requested.")
                % {"id": medication_request.get("id"), "status": medication_request.get("status")}
            )

        values = self._prescription_values(task, medication_request, patient, order_uuid, cache)
        Prescription = self.env["cdu.prescription"].sudo()
        existing = Prescription.search([("eregister_order_uuid", "=", order_uuid)], limit=1)
        if existing:
            self._handle_requested_again(existing, values)
            return existing, "updated"

        values.update({"state": "awaiting_verification", "fhir_sync_state": "pending"})
        prescription = Prescription.create(values)
        prescription.message_post(
            body=_(
                "Received from eRegister through OpenHIM (Task/%(task)s, MedicationRequest/%(mr)s)."
            )
            % {"task": task.get("id"), "mr": medication_request.get("id")}
        )
        return prescription, "created"

    def _prescription_values(self, task, medication_request, patient, order_uuid, cache):
        extensions = {
            extension.get("url"): extension for extension in medication_request.get("extension") or []
        }

        facility_reference = (
            (extensions.get(EXT_ORIGINATING_FACILITY) or {}).get("valueReference") or {}
        ).get("reference") or (task.get("requester") or {}).get("reference")
        facility = self._resolve_facility(facility_reference, cache)

        pickup_reference = (
            (extensions.get(EXT_PICKUP_POINT) or {}).get("valueReference") or {}
        ).get("reference")
        collection_point, pickup_point_name = self._resolve_pickup_point(pickup_reference, cache)

        patient_values = self._patient_values(patient)
        partner = self._find_or_create_partner(patient_values, facility)

        values = {
            "intake_source": "eregister_fhir",
            "eregister_order_uuid": order_uuid,
            "fhir_task_id": task.get("id"),
            "fhir_medication_request_id": medication_request.get("id"),
            "fhir_patient_id": patient.get("id"),
            "facility_id": facility.id,
            "facility_name": facility.name,
            "facility_code": facility.code,
            "prescription_date": self._date(medication_request.get("authoredOn")),
            "patient_id": partner.id,
            "patient_identifier": patient_values["eregister_id"],
            "hiv_program_id": patient_values["hiv_program_id"],
            "national_id": patient_values["national_id"],
            "patient_first_name": patient_values["name"],
            "patient_date_of_birth": patient_values["birth_date"],
            "patient_gender": patient_values["gender"],
            "patient_phone": patient_values["phone"],
            "secondary_contact": patient_values["secondary_phone"],
            "patient_address": patient_values["address"],
            "regimen_prescribed_raw": self._regimen_text(medication_request),
            "dosage_instructions": self._dosage_text(medication_request),
            "next_drug_pickup_date": self._date(
                (extensions.get(EXT_REQUESTED_PICKUP_DATE) or {}).get("valueDate")
            ),
            "next_clinical_visit_date": self._date(
                (extensions.get(EXT_NEXT_CLINICAL_VISIT_DATE) or {}).get("valueDate")
            ),
            "collection_point_id": collection_point.id if collection_point else False,
            "drug_pickup_point_raw": pickup_point_name or False,
            "prescriber_name": (medication_request.get("requester") or {}).get("display") or False,
        }
        values.update(self._allergy_values(patient.get("id")))
        if not values["prescription_date"]:
            raise UserError(_("The prescription has no authoredOn date."))
        return values

    def _handle_requested_again(self, prescription, values):
        """eRegister put the Task back to 'requested' for a prescription we hold."""
        if prescription.state == "rejected_to_facility" and not prescription.eregister_cancelled:
            self._apply_resubmission(prescription, values)
            return
        if prescription.eregister_cancelled:
            prescription.message_post(
                body=_("eRegister re-requested this prescription after cancelling it. The cancellation flag was cleared.")
            )
        else:
            prescription.message_post(
                body=_("eRegister re-sent this prescription. The current CDU status will be published again.")
            )
        prescription.write(
            {
                "eregister_cancelled": False,
                "fhir_task_id": values["fhir_task_id"],
                "fhir_sync_state": "pending",
                "fhir_sync_attempts": 0,
                "fhir_sync_error": False,
                "fhir_published_signature": False,
            }
        )

    def _apply_resubmission(self, prescription, values):
        """The facility corrected a returned prescription and re-sent it."""
        changed = []
        for field_name, new_value in values.items():
            if field_name in ("intake_source", "eregister_order_uuid"):
                continue
            field = prescription._fields[field_name]
            old_value = prescription[field_name]
            if field.type == "many2one":
                old_value = old_value.id
            elif field.type == "date" and old_value:
                old_value = fields.Date.to_string(old_value)
            if (old_value or False) != (new_value or False):
                changed.append(field.string)

        regimen_changed = (prescription.regimen_prescribed_raw or "") != (
            values.get("regimen_prescribed_raw") or ""
        )
        old_dosage = prescription.dosage_instructions or False
        new_dosage = values.get("dosage_instructions") or False
        prescription.write(
            {key: value for key, value in values.items() if key not in ("intake_source", "eregister_order_uuid")}
        )
        lines = prescription.product_line_ids.with_context(cdu_allow_product_line_sync=True)
        if regimen_changed:
            lines.unlink()
            prescription._seed_product_lines()
        elif old_dosage != new_dosage:
            # Product lines copy the prescription's dosage when seeded. Carry the
            # facility's new dosage onto lines that still have the old one; lines
            # a clerk has edited keep their dosage.
            lines.filtered(lambda line: (line.dosage_instructions or False) == old_dosage).write(
                {"dosage_instructions": new_dosage}
            )

        destination = prescription.rejected_from_state or (
            "awaiting_validation" if prescription.verified_at else "awaiting_verification"
        )
        if prescription.active_rejection_id:
            prescription.active_rejection_id.write(
                {
                    "is_active": False,
                    "returned_by": self.env.user.id,
                    "returned_at": fields.Datetime.now(),
                }
            )
        prescription.write({"state": destination, "active_rejection_id": False})
        prescription.message_post(
            body=_("Resubmitted by the facility in eRegister. Changed: %(changed)s. Returned to %(stage)s.")
            % {
                "changed": ", ".join(changed) or _("nothing"),
                "stage": dict(prescription._fields["state"].selection).get(destination, destination),
            }
        )

    # ------------------------------------------------------------------
    # Reference resolution
    # ------------------------------------------------------------------
    def _resolve(self, reference_object, index, expected_type):
        reference = (reference_object or {}).get("reference")
        if not reference:
            raise UserError(_("The Task has no %s reference.") % expected_type)
        resource = index.get(reference)
        if resource is None:
            resource = self.env["cdu.eregister.client"].read_reference(reference)
            index[reference] = resource
        if resource.get("resourceType") != expected_type:
            raise UserError(
                _("%(reference)s is not a %(type)s.") % {"reference": reference, "type": expected_type}
            )
        return resource

    def _resolve_facility(self, reference, cache):
        if not reference or not reference.startswith("Organization/"):
            raise UserError(_("The prescription has no originating facility."))
        if reference in cache:
            return cache[reference]
        Facility = self.env["cdu.facility"].sudo().with_context(active_test=False)
        # NAMING.md section 5: the Organization id is the facility code.
        code = reference.split("/", 1)[1]
        facility = Facility.search([("code", "=", code)], limit=1)
        if not facility:
            organization = self.env["cdu.eregister.client"].read_reference(reference)
            code = self._identifier(organization, FACILITY_CODE_SYSTEM) or code
            facility = Facility.search([("code", "=", code)], limit=1) or Facility.create(
                {"name": organization.get("name") or code, "code": code, "enabled": True}
            )
        cache[reference] = facility
        return facility

    def _resolve_pickup_point(self, reference, cache):
        """Match the pickup Location to a Collect & Go collection point.

        The Location id is 'pup-' + the Collect & Go location reference, which
        Odoo stores as remote_location_id. Some remote ids are shared by two
        collection points, so fall back to the Location name.
        """
        if not reference:
            return self.env["cdu.collection.point"], False
        if reference in cache:
            return cache[reference]
        CollectionPoint = self.env["cdu.collection.point"].sudo()
        location_id = reference.split("/", 1)[-1]
        code = location_id[len(PICKUP_POINT_ID_PREFIX):] if location_id.startswith(PICKUP_POINT_ID_PREFIX) else location_id
        matches = CollectionPoint.search([("remote_location_id", "=", code)])
        if len(matches) == 1:
            result = (matches, matches.name)
        else:
            location = self.env["cdu.eregister.client"].read_reference(reference)
            name = (location.get("name") or "").strip()
            if matches and name:
                matches = matches.filtered(lambda point: (point.name or "").strip().lower() == name.lower())
            elif name:
                matches = CollectionPoint.search([("name", "=ilike", name)])
            result = (matches if len(matches) == 1 else CollectionPoint, name or code)
        cache[reference] = result
        return result

    # ------------------------------------------------------------------
    # Patient
    # ------------------------------------------------------------------
    def _patient_values(self, patient):
        names = patient.get("name") or []
        name = next((item for item in names if item.get("use") == "official"), names[0] if names else {})
        full_name = name.get("text") or " ".join(
            part for part in (name.get("given") or []) + [name.get("family") or ""] if part
        )

        phones = sorted(
            (
                telecom
                for telecom in patient.get("telecom") or []
                if telecom.get("system") in ("phone", "sms") and telecom.get("value")
            ),
            key=lambda telecom: telecom.get("rank") or 999,
        )

        address = (patient.get("address") or [{}])[0]
        address_text = address.get("text") or ", ".join(
            part
            for part in (address.get("line") or []) + [address.get("city"), address.get("district")]
            if part
        )

        eregister_id = self._identifier(patient, EREGISTER_ID_SYSTEM)
        if not eregister_id:
            raise UserError(_("Patient/%s has no eRegister ID.") % patient.get("id"))
        return {
            "eregister_id": eregister_id,
            "hiv_program_id": self._identifier(patient, HIV_PROGRAM_ID_SYSTEM) or False,
            "national_id": self._identifier(patient, NATIONAL_ID_SYSTEM) or False,
            "name": full_name.strip() or eregister_id,
            "gender": {"male": "male", "female": "female", "other": "other"}.get(patient.get("gender"), False),
            "birth_date": self._date(patient.get("birthDate")),
            "phone": phones[0]["value"] if phones else False,
            "secondary_phone": phones[1]["value"] if len(phones) > 1 else False,
            "address": address_text or False,
        }

    def _find_or_create_partner(self, patient_values, facility):
        Partner = self.env["res.partner"].sudo().with_context(active_test=False)
        partner = Partner.search([("cdu_eregister_id", "=", patient_values["eregister_id"])], limit=1)
        partner_values = {
            "name": patient_values["name"],
            "cdu_eregister_id": patient_values["eregister_id"],
            "cdu_hiv_program_id": patient_values["hiv_program_id"],
            "cdu_national_id": patient_values["national_id"],
            "cdu_gender": patient_values["gender"],
            "cdu_date_of_birth": patient_values["birth_date"],
            "phone": patient_values["phone"],
            "cdu_secondary_contact": patient_values["secondary_phone"],
            "street": patient_values["address"],
            "cdu_source_facility_id": facility.id,
        }
        if not partner:
            partner_values["customer_rank"] = 1
            return Partner.create({key: value for key, value in partner_values.items() if value})
        # Never overwrite what the CDU has already corrected; only fill gaps.
        missing = {key: value for key, value in partner_values.items() if value and not partner[key]}
        if missing:
            partner.write(missing)
        return partner

    def _allergy_values(self, fhir_patient_id):
        try:
            bundle = self.env["cdu.eregister.client"].get(
                "AllergyIntolerance",
                params={"patient": "Patient/%s" % fhir_patient_id, "_count": 50},
            )
        except UserError:
            return {"has_allergies": "unknown"}
        labels = []
        any_allergy = False
        for entry in bundle.get("entry") or []:
            allergy = entry.get("resource") or {}
            clinical = self._first_code(allergy.get("clinicalStatus"))
            verification = self._first_code(allergy.get("verificationStatus"))
            if clinical in ("inactive", "resolved") or verification in ("refuted", "entered-in-error"):
                continue
            code = allergy.get("code") or {}
            codings = code.get("coding") or []
            if codings and all(coding.get("code") in NO_KNOWN_ALLERGY_CODES for coding in codings):
                continue
            any_allergy = True
            label = code.get("text") or next((coding.get("display") for coding in codings if coding.get("display")), False)
            if label:
                labels.append(label)
        if any_allergy:
            return {"has_allergies": "yes", "allergies": ", ".join(labels) or _("Recorded in eRegister")}
        if bundle.get("entry"):
            return {"has_allergies": "no", "allergies": False}
        return {"has_allergies": "unknown"}

    # ------------------------------------------------------------------
    # INT-03: publish fulfilment status
    # ------------------------------------------------------------------
    def push_prescription_status(self, prescription):
        """Publish the CDU state to the fulfilment Task. Returns (ok, message).

        Reads the Task first and writes it back with If-Match, so an eRegister
        change made in between (for example a cancellation) is never
        overwritten.
        """
        prescription = prescription.sudo()
        desired = prescription._fhir_desired_status()
        if not desired or not prescription.fhir_task_id or prescription.eregister_cancelled:
            if prescription.fhir_sync_state == "pending":
                prescription.write({"fhir_sync_state": "not_applicable"})
            return False, _("Nothing to publish for this prescription.")

        client = self.env["cdu.eregister.client"]
        path = "Task/%s" % prescription.fhir_task_id
        try:
            status_code, task = client._exchange(
                "GET", path, headers={"Cache-Control": "no-cache"}, prescription=prescription
            )
            if task.get("status") == "cancelled" and prescription._fhir_cdu_published_cancellation():
                prescription.write({"fhir_sync_state": "synced", "fhir_sync_attempts": 0, "fhir_sync_error": False})
                return True, _("The CDU cancellation is already published.")
            if task.get("status") == "cancelled":
                source = {"method": "GET", "url": client._endpoint(client._url(path)), "status_code": status_code}
                self._apply_eregister_cancellation(prescription, task, source)
                return False, _("eRegister has cancelled this prescription; nothing was published.")
            if task.get("status") in ("completed", "failed", "entered-in-error"):
                raise UserError(
                    _("The Task is '%s' in the repository; the CDU status was not published over it.")
                    % task.get("status")
                )
            version_id = (task.get("meta") or {}).get("versionId")
            client.put(path, self._task_with_status(task, desired), version_id=version_id, prescription=prescription)
        except UserError as exc:
            attempts = prescription.fhir_sync_attempts + 1
            max_attempts = self._max_publish_attempts()
            prescription.write(
                {
                    "fhir_sync_attempts": attempts,
                    "fhir_sync_state": "failed" if attempts >= max_attempts else "pending",
                    "fhir_sync_error": str(exc),
                }
            )
            return False, str(exc)

        task_status, business_status = desired[0], desired[1]
        prescription.write(
            {
                "fhir_task_status": task_status,
                "fhir_business_status": business_status,
                "fhir_published_signature": prescription._fhir_signature(desired),
                "fhir_sync_state": "synced",
                "fhir_sync_attempts": 0,
                "fhir_sync_error": False,
                "fhir_synced_at": fields.Datetime.now(),
            }
        )
        return True, _("Published '%s' to eRegister.") % FULFILMENT_STATUS_DISPLAY[business_status]

    def _task_with_status(self, task, desired):
        task_status, business_status, reason_codes, reason_text = desired
        task = copy.deepcopy(task)
        meta = task.get("meta") or {}
        for key in ("versionId", "lastUpdated", "source"):
            meta.pop(key, None)
        task["status"] = task_status
        display = FULFILMENT_STATUS_DISPLAY[business_status]
        task["businessStatus"] = {
            "coding": [{"system": FULFILMENT_STATUS_SYSTEM, "code": business_status, "display": display}],
            "text": display,
        }
        if reason_codes or reason_text:
            # R4 Task.statusReason holds one concept: the first coded reason,
            # with every selected reason in the text.
            status_reason = {"text": reason_text or reason_codes[0]}
            if reason_codes:
                status_reason["coding"] = [{"system": REJECTION_REASON_SYSTEM, "code": reason_codes[0]}]
            task["statusReason"] = status_reason
        else:
            task.pop("statusReason", None)
        task["lastModified"] = self._fhir_instant(fields.Datetime.now())
        return task

    def _max_publish_attempts(self):
        try:
            return max(int(self.env["cdu.eregister.client"]._param("cdu.eregister.max_publish_attempts", "10")), 1)
        except ValueError:
            return 10

    # ------------------------------------------------------------------
    # eRegister cancellations
    # ------------------------------------------------------------------
    def check_cancellations(self, commit=False):
        client = self.env["cdu.eregister.client"]
        params_model = self.env["ir.config_parameter"].sudo()
        started_at = fields.Datetime.now()
        params = {
            "owner": "Organization/%s" % client._cdu_organization_id(),
            "status": "cancelled",
            "_count": client._page_size(),
        }
        watermark = params_model.get_param("cdu.eregister.cancellation_watermark")
        if watermark:
            since = fields.Datetime.to_datetime(watermark) - CANCELLATION_OVERLAP
            params["_lastUpdated"] = "ge" + self._fhir_instant(since)

        _index, tasks, sources = client.search_all("Task", params, "CHECK_CANCELLATIONS")
        Prescription = self.env["cdu.prescription"].sudo()
        cancelled = 0
        for task in tasks:
            order_uuid = self._identifier(task, TASK_ID_SYSTEM)
            domain = (
                [("eregister_order_uuid", "=", order_uuid)]
                if order_uuid
                else [("fhir_task_id", "=", task.get("id"))]
            )
            prescription = Prescription.search(domain, limit=1)
            if (
                not prescription
                or prescription.eregister_cancelled
                or prescription._fhir_cdu_published_cancellation()
            ):
                continue
            with self.env.cr.savepoint():
                self._apply_eregister_cancellation(prescription, task, sources["Task/%s" % task.get("id")])
            cancelled += 1
            self._commit(commit)
        params_model.set_param("cdu.eregister.cancellation_watermark", fields.Datetime.to_string(started_at))
        self._commit(commit)
        return cancelled

    def _apply_eregister_cancellation(self, prescription, task, source):
        """`source` is the HTTP exchange that returned the cancelled Task."""
        if prescription.eregister_cancelled:
            return
        state_label = dict(prescription._fields["state"].selection).get(prescription.state, prescription.state)
        prescription.write(
            {
                "eregister_cancelled": True,
                "eregister_cancelled_at": fields.Datetime.now(),
                "fhir_task_status": task.get("status"),
                "fhir_business_status": self._first_code(task.get("businessStatus")) or False,
                "fhir_sync_state": "not_applicable",
                "fhir_sync_error": False,
            }
        )
        if prescription.state in PRE_PRODUCTION_STATES:
            if prescription.active_rejection_id:
                prescription.active_rejection_id.write({"is_active": False})
            prescription.write({"state": "cancelled", "active_rejection_id": False})
            message = _(
                "Cancelled in eRegister while %s. Production had not started, so the CDU prescription was cancelled automatically."
            ) % state_label
        elif prescription.state == "cancelled":
            message = _("Cancelled in eRegister. The CDU prescription was already cancelled.")
        else:
            message = _(
                "Cancelled in eRegister while %s. Production has started, so the CDU prescription was NOT cancelled. "
                "An admin must decide whether to stop it."
            ) % state_label
            prescription.activity_schedule(
                "mail.mail_activity_data_todo",
                summary=_("Prescription cancelled in eRegister"),
                note=message,
                user_id=self._admin_user().id,
            )
        prescription.message_post(body=message)
        self.env["cdu.eregister.client"].log_resource_outcome(
            "CHECK_CANCELLATIONS", source, task, True, prescription=prescription
        )

    def _admin_user(self):
        admins = self.env.ref("cdu_prescription.group_cdu_admin").users.filtered(
            lambda user: user.active and not user._is_superuser()
        )
        return admins[:1] or self.env.ref("base.user_admin")

    # ------------------------------------------------------------------
    # Small FHIR helpers
    # ------------------------------------------------------------------
    def _identifier(self, resource, system):
        return next(
            (
                identifier.get("value")
                for identifier in resource.get("identifier") or []
                if identifier.get("system") == system and identifier.get("value")
            ),
            False,
        )

    def _first_code(self, concept):
        return next((coding.get("code") for coding in (concept or {}).get("coding") or [] if coding.get("code")), False)

    def _date(self, value):
        """FHIR date or dateTime -> 'YYYY-MM-DD' (partial dates are dropped)."""
        if not value or len(value) < 10:
            return False
        try:
            return fields.Date.to_string(fields.Date.to_date(value[:10]))
        except ValueError:
            return False

    def _fhir_instant(self, value):
        return value.replace(tzinfo=timezone.utc, microsecond=0).isoformat()

    def _regimen_text(self, medication_request):
        concept = medication_request.get("medicationCodeableConcept") or {}
        coding = next((coding for coding in concept.get("coding") or [] if coding.get("code")), {})
        if coding:
            # Same "code = name" shape as the ART-094 report, so regimen mapping applies.
            return "%s = %s" % (coding["code"], coding["display"]) if coding.get("display") else coding["code"]
        return concept.get("text") or False

    def _dosage_text(self, medication_request):
        lines = [
            dosage.get("patientInstruction") or dosage.get("text")
            for dosage in medication_request.get("dosageInstruction") or []
        ]
        return "\n".join(line for line in lines if line) or False
