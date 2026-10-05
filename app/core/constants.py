"""Cross-domain constants that don't belong to any single module.

Keeping these in one place means the CSV importer, manual student creation,
and admin password-reset all agree on the same value without importing
from each other.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# ASSUMPTION — APPROVED BY OPERATOR, BUT FLAGGED FOR AWARENESS:
#
# Every student account (whether created via CSV import, manual admin
# creation, or an admin-triggered password reset) is issued this exact,
# fixed, publicly-documented password. must_change_password is set True in
# every case, which limits — but does NOT eliminate — the exposure window,
# because nothing in this codebase currently blocks API access before that
# flag is cleared (see app/core/dependencies.py — no such enforcement
# exists yet). Usernames are also predictable (`std-<cnic-digits>`), so a
# known CNIC is enough to attempt a login as that student with this
# password. Combined with the existing per-username rate limit, this is
# usable but not hardened. Recommended before production use: (1) enforce
# must_change_password server-side so a fresh account can call NOTHING but
# /auth/change-password until cleared, (2) deliver credentials out-of-band
# (SMS/email) rather than relying on the password being guessable-but-safe.
# ---------------------------------------------------------------------------
DEFAULT_STUDENT_PASSWORD = "vertex123"