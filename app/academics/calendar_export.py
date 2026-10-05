"""Calendar (.ics) files for class sessions -- the format every calendar app opens.

Built on the server so the headers are right and so the times are exact: each class is stored as an
institute wall-clock time (e.g. 08:30 in Karachi), which is converted to UTC here. A phone in any
timezone then shows the class at the correct moment, and Outlook / Google / Apple all accept it.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from app.core.config import settings


def _escape(text: str | None) -> str:
    return (str(text or "")).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def _fold(line: str) -> str:
    """RFC 5545: lines are at most 75 octets; longer ones continue on the next line after one space."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    parts, current = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(current) + len(b) > (75 if not parts else 74):
            parts.append(current)
            current = b""
        current += b
    parts.append(current)
    return "\r\n ".join(p.decode("utf-8") for p in parts)


def _utc(d: date, t: time) -> str:
    local = datetime.combine(d, t.replace(microsecond=0), tzinfo=ZoneInfo(settings.institute_timezone))
    return local.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_calendar(events: list[dict], *, name: str = "VIST timetable") -> bytes:
    """events: {id, course_code, course_title, session_date, start_time, end_time, room, teacher_name, title}"""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//VIST LMS//Timetable//EN", "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH", f"X-WR-CALNAME:{_escape(name)}",
    ]
    for e in events:
        description = "\n".join(x for x in (
            f"Teacher: {e['teacher_name']}" if e.get("teacher_name") else "",
            f"Topic: {e['title']}" if e.get("title") else "",
        ) if x)
        lines += [
            "BEGIN:VEVENT", f"UID:{e['id']}@vist-lms", f"DTSTAMP:{stamp}",
            f"DTSTART:{_utc(e['session_date'], e['start_time'])}", f"DTEND:{_utc(e['session_date'], e['end_time'])}",
            f"SUMMARY:{_escape(e['course_code'] + ' - ' + e['course_title'])}",
            f"LOCATION:{_escape('Room ' + e['room'] if e.get('room') else '')}",
        ]
        if description:
            lines.append(f"DESCRIPTION:{_escape(description)}")
        lines += [
            "STATUS:CONFIRMED", "BEGIN:VALARM", "TRIGGER:-PT15M", "ACTION:DISPLAY",
            f"DESCRIPTION:{_escape(e['course_code'] + ' starts in 15 minutes')}", "END:VALARM", "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return ("\r\n".join(_fold(line) for line in lines) + "\r\n").encode("utf-8")