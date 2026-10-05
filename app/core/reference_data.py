"""Shared dropdown reference data — provinces, districts, cities,
religions. Deliberately plain Python constants validated by Pydantic,
NOT Postgres ENUM columns — this codebase hit real, hard-to-undo
migration pain from DB-level enums multiple times (see the roll-number
and borrow-request-status migration history), and this data changes
more often than a role or a status ever would (new districts get
created, cities get added). A constant list is a one-line, no-migration
change; a DB enum would require ALTER TYPE every time.

ASSUMPTION / KNOWN LIMITATION: this district list is built from general
knowledge, not a live government source — I have no way to verify it
against Pakistan's current official gazette from inside this session.
Expanded from an earlier, notably incomplete pass (the former FATA
districts in Khyber Pakhtunkhwa — Bajaur, Khyber, Kurram, Mohmand,
North/South Waziristan, Orakzai — merged into KP in 2018 and were
missing entirely; several newer Punjab/Sindh/Balochistan districts
created by splitting older ones were also missing). Still may not be
100% current or exhaustive — provinces are stable and complete, but
district boundaries do occasionally change. Treat this the way you'd
treat any reference data you didn't personally source: spot-check it
against an official list before relying on it for anything beyond a
convenience dropdown, and extend the lists below as needed — nothing
else in the codebase has to change when you do.
"""
from __future__ import annotations

PAKISTAN_PROVINCES = [
    "Punjab",
    "Sindh",
    "Khyber Pakhtunkhwa",
    "Balochistan",
    "Islamabad Capital Territory",
    "Gilgit-Baltistan",
    "Azad Jammu and Kashmir",
]

PAKISTAN_DISTRICTS_BY_PROVINCE: dict[str, list[str]] = {
    "Punjab": [
        "Attock", "Bahawalnagar", "Bahawalpur", "Bhakkar", "Chakwal", "Chiniot", "Dera Ghazi Khan",
        "Faisalabad", "Gujranwala", "Gujrat", "Hafizabad", "Jhang", "Jhelum", "Kasur", "Khanewal",
        "Khushab", "Kot Addu", "Lahore", "Layyah", "Lodhran", "Mandi Bahauddin", "Mianwali", "Multan",
        "Murree", "Muzaffargarh", "Nankana Sahib", "Narowal", "Okara", "Pakpattan", "Rahim Yar Khan",
        "Rajanpur", "Rawalpindi", "Sahiwal", "Sargodha", "Sheikhupura", "Sialkot", "Talagang",
        "Taunsa", "Toba Tek Singh", "Vehari", "Wazirabad",
    ],
    "Sindh": [
        "Badin", "Dadu", "Ghotki", "Hyderabad", "Jacobabad", "Jamshoro", "Kambar Shahdadkot",
        "Karachi Central", "Karachi East", "Karachi South", "Karachi West", "Karachi Malir",
        "Karachi Korangi", "Kashmore", "Khairpur", "Larkana", "Matiari", "Mirpur Khas",
        "Naushahro Feroze", "Nawabshah", "Sanghar", "Shaheed Benazirabad", "Shikarpur", "Sujawal",
        "Sukkur", "Tando Allahyar", "Tando Muhammad Khan", "Tharparkar", "Thatta", "Umerkot",
    ],
    "Khyber Pakhtunkhwa": [
        "Abbottabad", "Bajaur", "Bannu", "Battagram", "Buner", "Charsadda", "Chitral",
        "Dera Ismail Khan", "Hangu", "Haripur", "Karak", "Khyber", "Kohat", "Kohistan", "Kurram",
        "Lakki Marwat", "Lower Dir", "Malakand", "Mansehra", "Mardan", "Mohmand", "North Waziristan",
        "Nowshera", "Orakzai", "Peshawar", "Shangla", "South Waziristan", "Swabi", "Swat", "Tank",
        "Torghar", "Upper Dir",
    ],
    "Balochistan": [
        "Awaran", "Barkhan", "Chagai", "Chaman", "Dera Bugti", "Duki", "Gwadar", "Harnai",
        "Jafarabad", "Jhal Magsi", "Kalat", "Kech", "Khuzdar", "Kharan", "Killa Abdullah",
        "Killa Saifullah", "Kohlu", "Lasbela", "Loralai", "Mastung", "Musakhel", "Nasirabad",
        "Nushki", "Panjgur", "Pishin", "Quetta", "Sherani", "Sibi", "Sohbatpur", "Washuk", "Zhob",
        "Ziarat",
    ],
    "Islamabad Capital Territory": ["Islamabad"],
    "Gilgit-Baltistan": [
        "Astore", "Diamer", "Ghanche", "Ghizer", "Gilgit", "Hunza", "Kharmang", "Nagar", "Shigar",
        "Skardu",
    ],
    "Azad Jammu and Kashmir": [
        "Bagh", "Bhimber", "Hattian Bala", "Haveli", "Kotli", "Mirpur", "Muzaffarabad", "Neelum",
        "Poonch", "Sudhnoti",
    ],
}

# Deliberately NOT tied to district selection — a straightforward
# "which city are you in" list covering the major population centers
# across all provinces, since a true district-to-city mapping would be
# far larger than is practical to maintain here.
MAJOR_CITIES = [
    "Abbottabad", "Bahawalpur", "Faisalabad", "Gilgit", "Gujranwala", "Gujrat", "Gwadar",
    "Hyderabad", "Islamabad", "Jhelum", "Karachi", "Lahore", "Larkana", "Mardan", "Multan",
    "Muzaffarabad", "Nawabshah", "Peshawar", "Quetta", "Rawalpindi", "Sahiwal", "Sargodha",
    "Sialkot", "Skardu", "Sukkur", "Swat", "Wah Cantonment",
]

RELIGIONS = ["Islam", "Christianity", "Hinduism", "Sikhism", "Other"]


def validate_province(value: str) -> str:
    v = value.strip()
    if v not in PAKISTAN_PROVINCES:
        raise ValueError(f"Province must be one of: {', '.join(PAKISTAN_PROVINCES)}.")
    return v


def validate_district(value: str, province: str | None = None) -> str:
    v = value.strip()
    if province and province in PAKISTAN_DISTRICTS_BY_PROVINCE:
        valid = PAKISTAN_DISTRICTS_BY_PROVINCE[province]
        if v not in valid:
            raise ValueError(f"District must be one of the districts of {province}.")
        return v
    all_districts = {d for districts in PAKISTAN_DISTRICTS_BY_PROVINCE.values() for d in districts}
    if v not in all_districts:
        raise ValueError("District must be a recognized Pakistani district.")
    return v


def validate_city(value: str) -> str:
    v = value.strip()
    if v not in MAJOR_CITIES:
        raise ValueError(f"City must be one of: {', '.join(MAJOR_CITIES)}.")
    return v


def validate_religion(value: str) -> str:
    v = value.strip()
    if v not in RELIGIONS:
        raise ValueError(f"Religion must be one of: {', '.join(RELIGIONS)}.")
    return v