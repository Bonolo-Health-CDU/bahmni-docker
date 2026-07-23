import re


E_LOCKER_DISTRICT_SELECTION = [
    ("Berea", "Berea"),
    ("Butha-Buthe", "Butha-Buthe"),
    ("Leribe", "Leribe"),
    ("Mafeteng", "Mafeteng"),
    ("Maseru", "Maseru"),
    ("Mohale's Hoek", "Mohale's Hoek"),
    ("Mokhotlong", "Mokhotlong"),
    ("Qacha's Nek", "Qacha's Nek"),
    ("Quthing", "Quthing"),
    ("Thaba-Tseka", "Thaba-Tseka"),
]


def _district_match_key(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().casefold())


E_LOCKER_DISTRICT_BY_MATCH_KEY = {
    _district_match_key(district): district
    for district, _label in E_LOCKER_DISTRICT_SELECTION
}
E_LOCKER_DISTRICT_BY_MATCH_KEY.update(
    {
        "mohaleshoek": "Mohale's Hoek",
        "mohalehoek": "Mohale's Hoek",
    }
)


def normalize_e_locker_district(value):
    """Return the canonical district spelling while preserving unknown input for validation."""
    if not value:
        return False
    stripped_value = str(value).strip()
    return E_LOCKER_DISTRICT_BY_MATCH_KEY.get(
        _district_match_key(stripped_value),
        stripped_value,
    )
