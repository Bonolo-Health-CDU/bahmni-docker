"""Collect & Go parcel tracking statuses (TrackingStatusType).

Codes and labels as provided by the Collect & Go team. Only confirmed
outcomes change a CDU prescription; unconfirmed ones are shown for
information until Collect & Go confirms them. In Collect & Go's terms the
"vendor" is the CDU.
"""

import re
from datetime import datetime

import pytz

TRACKING_STATUSES = {
    0: ("ProductionScheduled", "Production Scheduled"),
    1: ("InProduction", "In-Production"),
    2: ("ReadyForDispatch", "Ready for Dispatch"),
    3: ("AssignedToRun", "Assigned to Run"),
    4: ("LoadedOntoTruck", "Loaded Into Truck"),
    5: ("InTransit", "In-Transit"),
    6: ("DeliveredToPUPUC", "Delivered to PUP (Unconfirmed)"),
    7: ("DeliveredToPUPCO", "Delivered to PUP (Confirmed)"),
    8: ("DeliveredToPUPWLUC", "Delivered to the Wrong Location (Unconfirmed)"),
    9: ("DeliveredToPUPWLCO", "Delivered to the Wrong Location (Confirmed)"),
    10: ("ReturnedToCDUUC", "Returned to CDU (Unconfirmed)"),
    11: ("ReturnedToCDUCO", "Returned to CDU (Confirmed)"),
    12: ("CollectedByCustomer", "Collected by Customer"),
    13: ("DeliveredToPCUUC", "Delivered to PCU (Unconfirmed)"),
    14: ("DeliveredToPCUCO", "Delivered to PCU (Confirmed)"),
    15: ("ReadyForCollection", "Ready for Vendor Collection"),
    16: ("ReturnedToVendor", "Returned to Vendor"),
}

# What a status means for the CDU prescription.
COLLECTED = {12}  # the patient has the medicine
# Back at the CDU: confirmed return (11) or returned to the vendor, the CDU (16).
# 10 (unconfirmed) is information only.
RETURNED_TO_CDU = {11, 16}
AT_PICKUP_POINT = {6, 7, 13, 14}  # a PCU is treated like a pickup point
WRONG_LOCATION = {8, 9}  # an admin must follow up
READY_FOR_CDU_COLLECTION = {15}  # uncollected; the CDU must fetch it

# Statuses that need a person to act on the parcel.
NEEDS_ATTENTION = {
    **{code: "Parcel delivered to the wrong location" for code in WRONG_LOCATION},
    **{code: "Uncollected parcel ready for the CDU to fetch" for code in READY_FOR_CDU_COLLECTION},
}


def status_label(code):
    """Human label for a TrackingStatusType code."""
    if code in TRACKING_STATUSES:
        return TRACKING_STATUSES[code][1]
    return "Unknown status %s" % code if code is not None else "Status update received"


_TIMESTAMP = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?$")


def parse_status_date(value, timezone="Africa/Maseru"):
    """A Collect & Go timestamp as a naive UTC datetime (Odoo's convention).

    Collect & Go sends times like "2026-10-10T12:45:16.3": local time without
    a zone and with 1-7 fraction digits (Python 3.9 cannot parse those).
    Times without a zone are read in `timezone`.
    """
    if not isinstance(value, str):
        return None
    match = _TIMESTAMP.match(value.strip())
    if not match:
        return None
    day, clock, fraction, zone = match.groups()
    micro = int((fraction or "0")[:6].ljust(6, "0"))
    moment = datetime.strptime("%s %s" % (day, clock), "%Y-%m-%d %H:%M:%S").replace(microsecond=micro)
    if zone:
        if zone == "Z":
            offset_minutes = 0
        else:
            sign = 1 if zone[0] == "+" else -1
            digits = zone[1:].replace(":", "")
            offset_minutes = sign * (int(digits[:2]) * 60 + int(digits[2:]))
        moment = pytz.FixedOffset(offset_minutes).localize(moment)
    else:
        moment = pytz.timezone(timezone).localize(moment)
    return moment.astimezone(pytz.utc).replace(tzinfo=None)
