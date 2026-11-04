"""Make the keys for phone / browser push notifications (run once, then add the three lines to the server's .env).

    docker compose -f docker-compose.prod.yml run --rm --no-deps app python scripts/push_keys.py

Keep VAPID_PRIVATE_KEY secret. Changing the keys later means everyone has to turn notifications on again.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.push.webpush import generate_vapid_keys  # noqa: E402

public, private = generate_vapid_keys()
print("# Add these to .env, then restart the app:")
print(f"VAPID_PUBLIC_KEY={public}")
print(f"VAPID_PRIVATE_KEY={private}")
print("VAPID_SUBJECT=mailto:admin@your-institute.edu.pk   # change to a real address")