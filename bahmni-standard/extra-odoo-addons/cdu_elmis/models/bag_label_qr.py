"""Matching a scanned bag label QR to its parcel.

The QR holds the patient name and the prescription number on two lines.
USB scanners type what they read like a keyboard, and many do not type the
line break: they drop it, turn it into a space, or send Ctrl+J. A keyboard
layout can also change punctuation such as "/". So a scan is compared on its
letters and digits only, which every scanner types the same way.
"""

import unicodedata


def scan_key(value):
    """Letters and digits only, in lower case."""
    return "".join(char for char in str(value or "").casefold() if char.isalnum())


def scan_keys(value):
    """scan_key of the value, with accented letters as typed, folded (é → e) or dropped."""
    text = str(value or "")
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    dropped = "".join(char for char in text if char.isascii())
    return {scan_key(text), scan_key(folded), scan_key(dropped)}


def matches_bag_label(scanned_value, patient_name, prescription_number):
    """Whether a scan is this bag's QR, whatever the scanner did with the line break."""
    prescription = scan_key(prescription_number)
    if not prescription:
        return False
    expected = {name + prescription for name in scan_keys(patient_name)}
    return bool(scan_keys(scanned_value) & expected)
