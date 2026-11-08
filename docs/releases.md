# Versions and release notes

The version people see (menu footer in the staff portals, the status page `/health`, `/api/v1/system/version`) is the
**first entry of `app/releases.json`**. The deploy job adds which commit was installed and when (`app/build_info.json`),
so even an update without a new release entry shows its own build code and date on `/health`.

## With every change people will notice

Add an entry at the **top** of `app/releases.json` (a test checks the file):

```json
{
  "version": "1.5.0",
  "date": "2026-11-20",
  "title": "Short headline in plain words",
  "summary": "One or two sentences a non-technical person understands.",
  "changes": [
    {"kind": "new",      "for": "Teachers", "text": "What they can do now, and where to find it."},
    {"kind": "improved", "for": "Accounts", "text": "What works better than before."},
    {"kind": "fixed",    "for": "Admins",   "text": "What went wrong before, and that it works now."},
    {"kind": "security", "for": "Everyone", "text": "What is now better protected (no technical detail on the weakness)."}
  ]
}
```

Numbering: **1.4.0 → 1.5.0** when something new appears, **1.4.0 → 1.4.1** for fixes only, **2.0.0** for a change
that alters how people work every day. Write for the person using the system: what changed for them, where, and why it
matters. No file names, no code words.

After the update, each staff member sees a short "What's new" card once (per browser), and the full history is on
`/health` for anyone who is signed in. People who are not signed in see only whether the system works and the version.
