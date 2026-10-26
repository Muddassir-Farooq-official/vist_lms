"""Fail if a secret is about to be committed (audit SEC-01). Run in CI on every push and pull request.

Looks at every file git tracks: no .env files (except .env.example), no private keys or deploy keys, and no
connection strings or settings lines that carry a real-looking password or secret.
"""
from __future__ import annotations

import re
import subprocess
import sys

FORBIDDEN_NAMES = re.compile(r"(^|/)(\.env(\.[^/]*)?|deploy_key[^/]*|id_rsa[^/]*|id_ed25519[^/]*|[^/]*\.pem|[^/]*\.key)$")
ALLOWED_NAMES = {".env.example"}
PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "a private key"),
    (re.compile(r"postgres(?:ql)?(?:\+\w+)?://[^:\s/]+:(?!postgres@|password@|\$|\*|<|CHANGE)[^@\s]{6,}@(?!localhost|127\.0\.0\.1|db[:/]|postgres[:/])"),
     "a database URL with a password for a real server"),
    (re.compile(r"^(JWT_SECRET|SECRET_KEY|SMS_API_KEY|SMS_PASSWORD|ONELINK_\w*SECRET\w*)\s*=\s*['\"]?(?!change|CHANGE|ci-|test|example|<|\$)[A-Za-z0-9+/_=-]{16,}", re.M),
     "a secret value in a settings line"),
]
SKIP_DIRS = ("tests/", "static/vendor/")


def main() -> int:
    files = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout.split()
    problems = []
    for f in files:
        name = f.rsplit("/", 1)[-1]
        if FORBIDDEN_NAMES.search(f) and name not in ALLOWED_NAMES:
            problems.append(f"{f}: this kind of file must never be committed")
            continue
        if f.startswith(SKIP_DIRS) or f.endswith((".png", ".jpg", ".pdf", ".woff2", ".ico", ".lock")):
            continue
        try:
            text = open(f, encoding="utf-8").read()
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for rx, what in PATTERNS:
            if rx.search(text):
                problems.append(f"{f}: looks like {what}")
    if problems:
        print("Secrets check FAILED:\n  " + "\n  ".join(problems))
        print("Remove them, rotate anything that was exposed, and keep real values only in the server's .env.")
        return 1
    print(f"Secrets check passed ({len(files)} files).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
