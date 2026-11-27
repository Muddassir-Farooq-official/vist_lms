"""Cross-domain constants that don't belong to any single module.

Keeping these in one place means the CSV importer, manual student creation,
and admin password-reset all agree on the same value without importing
from each other.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# The old shared first password for students. New students no longer get it: each gets their own random one-time
# password (app/core/security.new_temp_password), handed over once and changed at first login. It is kept only so the
# password-change screens refuse it as a new password.
# ---------------------------------------------------------------------------
DEFAULT_STUDENT_PASSWORD = "vertex123"