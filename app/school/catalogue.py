"""The ready list of school classes and their usual subjects: Pakistani boards (Playgroup to Class 12,
with Matric and Intermediate groups) and Cambridge (O Level, AS and A2).

Schools differ, so this is only a starting point: after setting up, the admin adds or removes subjects
for any class. Subject codes are short (ENG, MATH ...); the course code becomes CLASS-SUBJECT, e.g. C9S-PHY.
"""
from __future__ import annotations

S = {  # subject code -> title
    "ENG": "English", "URD": "Urdu", "MATH": "Mathematics", "GK": "General Knowledge", "ISL": "Islamiat", "ART": "Drawing & Art",
    "COMP": "Computer", "GSCI": "General Science", "SST": "Social Studies", "TQ": "Tarjuma-tul-Quran", "PST": "Pakistan Studies",
    "PHY": "Physics", "CHEM": "Chemistry", "BIO": "Biology", "GMATH": "General Mathematics", "CIV": "Civics", "EDU": "Education",
    "ECO": "Economics", "ACC": "Principles of Accounting", "COM": "Principles of Commerce", "BMATH": "Business Mathematics",
    "BANK": "Banking", "STAT": "Business Statistics", "CGEO": "Commercial Geography", "CS": "Computer Science",
    "BUS": "Business Studies", "PSY": "Psychology", "SOC": "Sociology", "GP": "General Paper",
}

PRE = ["ENG", "URD", "MATH", "GK", "ISL", "ART"]
LOWER = ["ENG", "URD", "MATH", "GK", "ISL", "COMP"]
UPPER = ["ENG", "URD", "MATH", "GSCI", "SST", "ISL", "COMP"]

STAGES = [
    ("PRE_SCHOOL", "Pre-school"), ("PRIMARY", "Primary (Class 1-5)"), ("MIDDLE", "Middle (Class 6-8)"),
    ("MATRIC", "Matric (Class 9-10)"), ("INTER", "Intermediate (Class 11-12)"), ("O_LEVEL", "O Level"), ("A_LEVEL", "A Level"),
]


def _cls(code, name, level, stage, subjects, *, stream=None, optional=(), next_code=None):
    return {"code": code, "name": name, "level": level, "stage": stage, "stream": stream,
            "subjects": [{"code": s, "title": S[s], "optional": s in optional} for s in subjects], "next": next_code}


def _matric(tag, stream, electives):
    return [_cls(f"C9{tag}", f"Class 9 ({stream})", 12, "MATRIC", ["ENG", "URD", "ISL", "TQ"] + electives, stream=stream, next_code=f"C10{tag}"),
            _cls(f"C10{tag}", f"Class 10 ({stream})", 13, "MATRIC", ["ENG", "URD", "PST", "TQ"] + electives, stream=stream)]


def _inter(tag, stream, first, second=None):
    return [_cls(f"C11{tag}", f"Class 11 ({stream})", 14, "INTER", ["ENG", "URD", "ISL", "TQ"] + first, stream=stream, next_code=f"C12{tag}"),
            _cls(f"C12{tag}", f"Class 12 ({stream})", 15, "INTER", ["ENG", "URD", "PST", "TQ"] + (second or first), stream=stream)]


O_SUBJECTS = ["ENG", "URD", "ISL", "PST", "MATH", "PHY", "CHEM", "BIO", "CS", "ACC", "ECO", "BUS"]
O_OPTIONAL = ("PHY", "CHEM", "BIO", "CS", "ACC", "ECO", "BUS")
A_SUBJECTS = ["MATH", "PHY", "CHEM", "BIO", "CS", "ECO", "ACC", "BUS", "PSY", "SOC", "GP"]

# The groups the admin ticks in the set-up form, each giving one or more classes.
GROUPS: list[dict] = [
    {"key": "PRE", "stage": "PRE_SCHOOL", "label": "Playgroup, Nursery, Prep / KG", "classes": [
        _cls("PG", "Playgroup", 1, "PRE_SCHOOL", PRE, next_code="NUR"),
        _cls("NUR", "Nursery", 2, "PRE_SCHOOL", PRE, next_code="KG"),
        _cls("KG", "Prep / KG", 3, "PRE_SCHOOL", PRE, next_code="C1")]},
    {"key": "PRIMARY", "stage": "PRIMARY", "label": "Class 1 to 5", "classes": [
        _cls(f"C{n}", f"Class {n}", 3 + n, "PRIMARY", LOWER if n <= 3 else UPPER, next_code=f"C{n + 1}") for n in range(1, 6)]},
    {"key": "MIDDLE", "stage": "MIDDLE", "label": "Class 6 to 8", "classes": [
        _cls(f"C{n}", f"Class {n}", 3 + n, "MIDDLE", UPPER, next_code=f"C{n + 1}" if n < 8 else None) for n in range(6, 9)]},
    {"key": "MATRIC_SCI", "stage": "MATRIC", "label": "Class 9-10 Science (Biology)", "classes": _matric("S", "Science", ["MATH", "PHY", "CHEM", "BIO"])},
    {"key": "MATRIC_CS", "stage": "MATRIC", "label": "Class 9-10 Computer Science", "classes": _matric("C", "Computer Science", ["MATH", "PHY", "CHEM", "COMP"])},
    {"key": "MATRIC_ARTS", "stage": "MATRIC", "label": "Class 9-10 Arts / Humanities", "classes": _matric("A", "Arts", ["GMATH", "GSCI", "CIV", "EDU"])},
    {"key": "INTER_PM", "stage": "INTER", "label": "Class 11-12 Pre-Medical (F.Sc.)", "classes": _inter("PM", "Pre-Medical", ["PHY", "CHEM", "BIO"])},
    {"key": "INTER_PE", "stage": "INTER", "label": "Class 11-12 Pre-Engineering (F.Sc.)", "classes": _inter("PE", "Pre-Engineering", ["PHY", "CHEM", "MATH"])},
    {"key": "INTER_ICS", "stage": "INTER", "label": "Class 11-12 ICS", "classes": _inter("CS", "ICS", ["MATH", "PHY", "COMP"])},
    {"key": "INTER_ICOM", "stage": "INTER", "label": "Class 11-12 I.Com", "classes": _inter("CM", "I.Com", ["ACC", "ECO", "COM", "BMATH"], ["ACC", "BANK", "STAT", "CGEO"])},
    {"key": "INTER_FA", "stage": "INTER", "label": "Class 11-12 F.A. (Arts)", "classes": _inter("FA", "F.A.", ["CIV", "ECO", "EDU"])},
    {"key": "O_LEVEL", "stage": "O_LEVEL", "label": "O Level (O1, O2, O3)", "classes": [
        _cls(f"O{n}", f"O Level {n}", 11 + n, "O_LEVEL", O_SUBJECTS, optional=O_OPTIONAL, next_code=f"O{n + 1}" if n < 3 else None) for n in range(1, 4)]},
    {"key": "A_LEVEL", "stage": "A_LEVEL", "label": "A Level (AS, A2)", "classes": [
        _cls("AS", "A Level AS", 15, "A_LEVEL", A_SUBJECTS, optional=tuple(A_SUBJECTS), next_code="A2"),
        _cls("A2", "A Level A2", 16, "A_LEVEL", A_SUBJECTS, optional=tuple(A_SUBJECTS))]},
]
GROUP_BY_KEY = {g["key"]: g for g in GROUPS}


def summary() -> list[dict]:
    """For the set-up form."""
    stage_label = dict(STAGES)
    return [{"key": g["key"], "stage": g["stage"], "stage_label": stage_label[g["stage"]], "label": g["label"],
             "classes": [{"code": c["code"], "name": c["name"], "subjects": [s["title"] + (" (choice)" if s["optional"] else "") for s in c["subjects"]]}
                         for c in g["classes"]]} for g in GROUPS]
