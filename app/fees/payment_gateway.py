"""Payment gateway abstraction layer.

VIST has applied for 1LINK 1Bill biller API access through its bank but
does not have credentials yet. Everything downstream of this module
(challan generation, the student "pay" action, the webhook endpoint) is
built against the PaymentGatewayProvider interface below, not against any
specific gateway's wire format. When real 1LINK credentials arrive, the
only change needed is: implement a real provider class in this file and
flip which one get_payment_gateway() returns. No router, service, or
frontend code changes required — they all just call get_payment_gateway().
"""
from __future__ import annotations

import secrets
import uuid
from abc import ABC, abstractmethod
from datetime import date, datetime, timezone
from functools import lru_cache
from typing import Any


class PaymentGatewayError(Exception):
    pass


class PaymentGatewayProvider(ABC):
    """What every provider (simulated today, real 1LINK tomorrow) must
    implement. Kept deliberately small — the two operations a biller
    aggregator integration actually needs: mint a payable reference
    number for a specific charge, and (meaningful for the simulated
    provider only) a way to pretend a payment happened. A real provider
    wouldn't implement simulate_payment meaningfully — real payments
    arrive via the webhook endpoint, driven by the bank's callback, not
    by us calling out."""

    @abstractmethod
    async def generate_consumer_number(self, *, reference: str, amount: float, due_date: date) -> str: ...

    @abstractmethod
    async def simulate_payment(self, consumer_number: str) -> dict[str, Any]: ...


class SimulatedOneLinkProvider(PaymentGatewayProvider):
    """Stand-in for the real 1LINK 1Bill API. Consumer number format is a
    plausible placeholder — a fixed biller-code prefix (would be VIST's
    actual 1LINK-issued biller code in production), an encoded reference,
    and a simple mod-10 check digit, matching the general shape of real
    1Bill consumer numbers (a fixed-length all-numeric string a payer
    types at any bank/ATM/OTC agent). The exact format 1LINK assigns
    depends on VIST's actual biller registration — this is not meant to
    be bit-for-bit correct, only structurally realistic enough to build
    and test the rest of the system against today."""

    BILLER_CODE = "9821"  # placeholder — replace with VIST's real assigned biller code

    def _check_digit(self, digits: str) -> str:
        total = sum(int(d) * (2 if i % 2 == 0 else 1) for i, d in enumerate(digits))
        return str((10 - (total % 10)) % 10)

    async def generate_consumer_number(self, *, reference: str, amount: float, due_date: date) -> str:
        # All 12 body digits come from the RNG directly — previously the
        # last 4 came from uuid4().hex[:4].upper(), which can contain
        # letters (A-F) and silently produced a consumer number that
        # wasn't actually all-numeric, contradicting this class's own
        # docstring ("a fixed-length all-numeric string a payer types at
        # any bank/ATM/OTC agent" — an ATM keypad has no letter keys).
        body = "".join(str(secrets.randbelow(10)) for _ in range(12))
        check = self._check_digit(body)
        return f"{self.BILLER_CODE}{body}{check}"

    async def simulate_payment(self, consumer_number: str) -> dict[str, Any]:
        return {
            "success": True,
            "gateway_reference": f"SIM-{uuid.uuid4().hex[:12].upper()}",
            "paid_at": datetime.now(timezone.utc),
        }


@lru_cache
def get_payment_gateway() -> PaymentGatewayProvider:
    """Single point of truth for which provider is active. Swapping to
    real 1LINK later means adding a real provider class above and
    returning it here — nothing else in the codebase imports a provider
    class directly."""
    return SimulatedOneLinkProvider()