import logging


_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # Preserve the daily dose that was already used by historical/draft
    # prescription requirements. Invalid legacy values fall back to the
    # operational default without altering clinical dosage instructions.
    cr.execute(
        """
        UPDATE cdu_picking_patient_resolution resolution
           SET daily_dose = CASE
                   WHEN patient.daily_dose > 0
                    AND patient.daily_dose = FLOOR(patient.daily_dose)
                   THEN patient.daily_dose::integer
                   ELSE 1
               END,
               original_daily_dose = CASE
                   WHEN patient.daily_dose > 0
                    AND patient.daily_dose = FLOOR(patient.daily_dose)
                   THEN patient.daily_dose::integer
                   ELSE 1
               END
          FROM cdu_batch_patient_line patient
         WHERE resolution.patient_line_id = patient.id
        """
    )
    _logger.info(
        "CDU picking migration initialized prescription-level daily units "
        "from existing operational requirements."
    )
