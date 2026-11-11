"""How each rule is shown on Settings: its section, the plain label, and the right control.

Nothing here changes what is stored (still the number in policy_settings); it only decides how a person sees and edits it:
  switch   -- on / off (stored 1 / 0)
  choice   -- a dropdown of named options (stored value of the chosen option); `custom` adds "Custom..." with a number
  number   -- a number box with a unit before (Rs.) or after (days, %, minutes ...)
  optional -- a switch that turns a limit on, plus its number; off stores 0 ("no limit", "never")
"""
from __future__ import annotations

SECTIONS: tuple[tuple[str, str], ...] = (
    ("Institute type", "A school, or a college / university. A school gets Classes & sections and school words in the menus."),
    ("Fees & fines", "Late fees, how they grow, and when they become fines to collect."),
    ("Sign-in security", "The code from an authenticator app that administrators type after their password."),
    ("Attendance", "Marking classes, alerts for teachers, and the limits that affect students."),
    ("Exams & results", "Admit cards, graduation and supplementary exams."),
    ("Promotion", "What a student needs to move to the next semester."),
    ("Payroll", "Leaves, late marks and deductions on monthly pay."),
    ("Purchases", "Approvals for buying."),
    ("SMS messages", "Which events send a message. With WhatsApp set up (SMS & WhatsApp page) it goes by WhatsApp, and by SMS only if WhatsApp fails."),
    ("Certificates", "Fees for certificates students ask for from their portal."),
    ("Notifications", "The sound the portal plays for a new notification, for everyone, and how alerts on phones behave."),
    ("Reports", "The monthly report for the principal."),
    ("Staff tasks", "Reminders for daily to-do lists."),
    ("Dashboard display", "How amounts appear on dashboard cards."),
)

HOURS = [[0, "Never"]] + [[h, f"{(h - 1) % 12 + 1}:00 {'AM' if h < 12 else 'PM'}"] for h in range(1, 24)]

UI: dict[str, dict] = {
    "school_mode": {"section": "Institute type", "label": "This institute is a", "control": "choice",
                    "choices": [[0, "College / University"], [1, "School"]]},
    # ---------------------------------------------------------------- fees
    "fee_late_fee_grace_days": {"section": "Fees & fines", "sub": "Late fee", "label": "Grace days after the due date",
                                "control": "number", "after": "days"},
    "fee_late_fee_double_every_days": {"section": "Fees & fines", "sub": "Late fee", "label": "Double the daily rate",
                                       "control": "choice", "custom": "days",
                                       "choices": [[0, "Never (one flat rate)"], [7, "Every week"], [14, "Every 2 weeks"], [30, "Every month"]]},
    "fee_late_fee_max_per_day": {"section": "Fees & fines", "sub": "Late fee", "label": "Highest daily rate", "control": "optional",
                                 "before": "Rs.", "after": "/ day", "off": "No limit", "on": "Limit it"},
    "fee_late_fee_cap": {"section": "Fees & fines", "sub": "Late fee", "label": "Most one instalment can build up", "control": "optional",
                         "before": "Rs.", "off": "No limit", "on": "Limit it"},
    "fee_backdated_grace_days": {"section": "Fees & fines", "sub": "Late fee", "label": "Grace for instalments created after their due date",
                                 "control": "number", "after": "days"},
    "fee_late_fee_auto_post": {"section": "Fees & fines", "sub": "Collecting", "label": "Add the late fee to Fines when a late instalment is paid",
                               "control": "switch"},
    "one_bill_convenience_fixed": {"section": "Fees & fines", "sub": "Online payment (1Bill)", "label": "Charge added to each online payment",
                                   "control": "number", "before": "Rs."},
    "one_bill_convenience_percent": {"section": "Fees & fines", "sub": "Online payment (1Bill)", "label": "Plus a percentage of the challan",
                                     "control": "number", "after": "%"},
    "fee_payment_max_backdate_days": {"section": "Fees & fines", "sub": "Collecting", "label": "Payments may be dated back at most",
                                      "control": "number", "after": "days"},
    # ---------------------------------------------------------------- sign-in security
    "two_factor_required_for_admins": {"section": "Sign-in security", "label": "Administrators must use two-step sign-in", "control": "switch"},
    "two_factor_remember_days": {"section": "Sign-in security", "label": "Remember a device for", "control": "choice",
                                 "choices": [[1, "1 day"], [7, "7 days"], [30, "30 days"], [90, "90 days"]]},
    # ---------------------------------------------------------------- attendance
    "attendance_marking_window_minutes": {"section": "Attendance", "sub": "Teachers", "label": "Time a teacher has to mark a class",
                                          "control": "number", "after": "minutes"},
    "missed_class_alerts": {"section": "Attendance", "sub": "Teachers", "label": "Alert the administration when a teacher on campus misses a class",
                            "control": "switch"},
    "attendance_eligibility_threshold_percent": {"section": "Attendance", "sub": "Students", "label": "Minimum attendance to pass a course",
                                                 "control": "number", "after": "%"},
    "attendance_fine_mode": {"section": "Attendance", "sub": "Attendance fines", "label": "How attendance fines work", "control": "choice",
                             "choices": [[1, "Per subject, by band (before final exams)"], [0, "One fine per term, at attendance lock (older rule)"]]},
    "attendance_band1_from": {"section": "Attendance", "sub": "Attendance fines", "label": "Band 1 starts at", "control": "number", "after": "%"},
    "attendance_band1_amount": {"section": "Attendance", "sub": "Attendance fines", "label": "Band 1 fine", "control": "number", "before": "Rs.", "after": "per subject"},
    "attendance_band2_from": {"section": "Attendance", "sub": "Attendance fines", "label": "Band 2 starts at", "control": "number", "after": "%"},
    "attendance_band2_amount": {"section": "Attendance", "sub": "Attendance fines", "label": "Band 2 fine", "control": "number", "before": "Rs.", "after": "per subject"},
    "attendance_band3_from": {"section": "Attendance", "sub": "Attendance fines", "label": "Band 3 starts at", "control": "number", "after": "%"},
    "attendance_band3_amount": {"section": "Attendance", "sub": "Attendance fines", "label": "Band 3 fine", "control": "number", "before": "Rs.", "after": "per subject"},
    "attendance_weekly_warnings": {"section": "Attendance", "sub": "Attendance fines", "label": "Warn students every Monday while a subject is in a fine band",
                                   "control": "switch"},
    "attendance_warning_min_classes": {"section": "Attendance", "sub": "Attendance fines", "label": "Start warning after", "control": "number",
                                       "after": "classes of the subject"},
    "attendance_drop_threshold_percent": {"section": "Attendance", "sub": "Older rule: one fine per term", "label": "Drop from the course at or below",
                                          "control": "number", "after": "%"},
    "attendance_fine_threshold_percent": {"section": "Attendance", "sub": "Older rule: one fine per term", "label": "Fine students at or below",
                                          "control": "number", "after": "%"},
    "attendance_fine_amount": {"section": "Attendance", "sub": "Older rule: one fine per term", "label": "Attendance fine", "control": "number", "before": "Rs."},
    "withdrawal_deadline_days": {"section": "Attendance", "sub": "Students", "label": "Students may withdraw from a course until",
                                 "control": "number", "after": "days after the term starts"},
    # ---------------------------------------------------------------- exams
    "admit_card_requires_fees_cleared": {"section": "Exams & results", "label": "Hold the admit card while fees are overdue", "control": "switch"},
    "exam_students_per_invigilator": {"section": "Exams & results", "label": "One teacher on exam duty for every", "control": "number",
                                      "after": "students"},
    "graduation_min_cgpa": {"section": "Exams & results", "label": "Lowest CGPA that can graduate", "control": "number", "after": "out of 4.00"},
    "supplementary_max_grade_points": {"section": "Exams & results", "label": "Highest grade points a supplementary pass can earn",
                                       "control": "optional", "after": "grade points", "off": "No cap", "on": "Cap it"},
    # ---------------------------------------------------------------- promotion
    "promotion_min_cgpa": {"section": "Promotion", "label": "Minimum CGPA to move up", "control": "number", "after": "out of 4.00"},
    "promotion_max_courses_to_clear": {"section": "Promotion", "label": "Most failed courses a student may still carry",
                                       "control": "number", "after": "courses"},
    # ---------------------------------------------------------------- payroll
    "payroll_attendance_deductions": {"section": "Payroll", "sub": "Attendance and pay", "label": "Deduct absences and late arrivals from pay",
                                      "control": "switch"},
    "payroll_casual_leaves_teacher": {"section": "Payroll", "sub": "Attendance and pay", "label": "Casual leaves a month: teachers",
                                      "control": "number", "after": "days"},
    "payroll_casual_leaves_staff": {"section": "Payroll", "sub": "Attendance and pay", "label": "Casual leaves a month: other staff",
                                    "control": "number", "after": "days"},
    "payroll_sick_leaves_per_year": {"section": "Payroll", "sub": "Attendance and pay", "label": "Paid sick leave a year", "control": "number", "after": "days"},
    "payroll_late_marks_per_day": {"section": "Payroll", "sub": "Attendance and pay", "label": "Late arrivals that cost one day's pay",
                                   "control": "number", "after": "late marks"},
    "payroll_missing_checkout_counts": {"section": "Payroll", "sub": "Attendance and pay", "label": "A day with no check-out counts as leaving early",
                                        "control": "switch"},
    "payroll_default_working_days": {"section": "Payroll", "sub": "Attendance and pay", "label": "Working days in a month", "control": "optional",
                                     "after": "days", "off": "Each person's own timetable", "on": "Same for everyone"},
    "payroll_security_percent": {"section": "Payroll", "sub": "Deductions and advances", "label": "Security deposit held each month",
                                 "control": "number", "after": "% of basic"},
    "payroll_advance_max_salary_multiple": {"section": "Payroll", "sub": "Deductions and advances", "label": "Most one employee may owe in advances",
                                            "control": "number", "after": "× basic salary"},
    # ---------------------------------------------------------------- purchases
    "purchase_second_approval_above": {"section": "Purchases", "label": "A second administrator approves purchases above", "control": "optional",
                                       "before": "Rs.", "off": "Never needed", "on": "Above an amount"},
    "purchase_over_estimate_percent": {"section": "Purchases", "label": "Re-approve when the price is more than the estimate by",
                                       "control": "number", "after": "%"},
    # ---------------------------------------------------------------- sms
    "certificate_fee_bonafide": {"section": "Certificates", "label": "Bonafide certificate", "control": "number", "before": "Rs."},
    "certificate_fee_character": {"section": "Certificates", "label": "Character certificate", "control": "number", "before": "Rs."},
    "certificate_fee_completion": {"section": "Certificates", "label": "Completion certificate", "control": "number", "before": "Rs."},
    "certificate_fee_provisional": {"section": "Certificates", "label": "Provisional certificate (result)", "control": "number", "before": "Rs."},
    "certificate_fee_migration": {"section": "Certificates", "label": "Migration certificate / NOC", "control": "number", "before": "Rs."},
    "certificate_fee_fee_clearance": {"section": "Certificates", "label": "Fee clearance letter", "control": "number", "before": "Rs."},
    "monthly_report_notice": {"section": "Reports", "label": "Tell the principal on the 1st that last month's report is ready", "control": "switch"},
    "sms_on_fee_overdue": {"section": "SMS messages", "label": "Weekly reminder for overdue fees", "control": "switch"},
    "sms_on_fee_reminder": {"section": "SMS messages", "label": "Fee reminder before the due date", "control": "switch"},
    "sms_on_exam_duty": {"section": "SMS messages", "label": "A teacher is put on exam duty (to the teacher)", "control": "switch"},
    "sms_on_warning": {"section": "SMS messages", "label": "A student is given a warning (to the guardian)", "control": "switch"},
    "sms_on_fee_paid": {"section": "SMS messages", "label": "Fee payment received (with the receipt on WhatsApp)", "control": "switch"},
    "sms_on_certificate_ready": {"section": "SMS messages", "label": "Certificate ready (with the certificate on WhatsApp)", "control": "switch"},
    "sms_on_parent_weekly": {"section": "SMS messages", "label": "Monday note to parents when a child needs attention", "control": "switch"},
    "sms_on_attendance_fine": {"section": "SMS messages", "label": "When an attendance fine is issued", "control": "switch"},
    "sms_on_attendance_warning": {"section": "SMS messages", "label": "Weekly attendance warning", "control": "switch"},
    "sms_on_results": {"section": "SMS messages", "label": "When results are published", "control": "switch"},
    "sms_on_datesheet": {"section": "SMS messages", "label": "When a datesheet is published", "control": "switch"},
    "sms_on_admission": {"section": "SMS messages", "label": "Applicants selected in a merit list", "control": "switch"},
    "sms_on_security_alert": {"section": "SMS messages", "label": "Security alerts to administrators", "control": "switch"},
    "sms_to_guardian": {"section": "SMS messages", "label": "Also send each message to the guardian", "control": "switch"},
    # ---------------------------------------------------------------- other
    "notify_sound_tone": {"section": "Notifications", "sub": "Sound in the portal", "label": "Tone for a new notification", "control": "choice", "preview": "sound",
                          "choices": [[0, "Off (no sound)"], [1, "Soft chime"], [2, "Bell"], [3, "Marimba"], [4, "Digital beep"], [5, "Phone ring (long, about 5 s)"], [6, "Melody (long, about 4 s)"]]},
    "notify_sound_urgent_tone": {"section": "Notifications", "sub": "Sound in the portal", "label": "Tone for urgent notices and final warnings", "control": "choice",
                                 "preview": "sound", "choices": [[0, "Off (no sound)"], [1, "Soft chime"], [2, "Bell"], [3, "Marimba"], [4, "Digital beep"], [5, "Phone ring (long, about 5 s)"], [6, "Melody (long, about 4 s)"], [7, "Alarm (long, about 4 s)"]]},
    "notify_sound_volume": {"section": "Notifications", "sub": "Sound in the portal", "label": "Volume", "control": "number", "after": "%"},
    "notify_sound_repeat": {"section": "Notifications", "sub": "Sound in the portal", "label": "Play it", "control": "choice",
                            "choices": [[1, "Once"], [2, "Twice"], [3, "3 times"], [5, "5 times (like a ringtone)"]]},
    "phone_alert_vibrate": {"section": "Notifications", "sub": "Phones", "label": "Vibrate (Android): longer for urgent notices", "control": "switch"},
    "phone_alert_keep_urgent": {"section": "Notifications", "sub": "Phones", "label": "Urgent and important alerts stay on screen until tapped", "control": "switch"},
    "todo_reminder_hour": {"section": "Staff tasks", "label": "Remind staff with no to-do list for today, from", "control": "choice", "choices": HOURS},
    "amount_display": {"section": "Dashboard display", "label": "Amounts on dashboard cards", "control": "choice",
                       "choices": [[0, "Full amount (Rs. 4,045,000)"], [1, "K / M (Rs. 4.05M)"], [2, "Lakh / crore (Rs. 40.45 lakh)"]]},
}


def ui_for(key: str, group: str, label: str, minimum: float, maximum: float) -> dict:
    """The presentation of one rule. A rule added later without an entry here still gets a sensible control."""
    u = dict(UI.get(key) or {})
    if not u:
        u = {"section": group, "label": label}
        u["control"] = "switch" if (minimum, maximum) == (0, 1) else "number"
    u.setdefault("label", label)
    if u["section"] not in {s for s, _ in SECTIONS}:
        u["section"] = group
    return u
