from __future__ import annotations

import asyncio
from datetime import date

from app.fees.payment_gateway import SimulatedOneLinkProvider


def _generate() -> str:
    provider = SimulatedOneLinkProvider()
    return asyncio.run(
        provider.generate_consumer_number(reference="STUDENT-abcd1234-1", amount=1000.0, due_date=date(2026, 9, 1))
    )


class TestConsumerNumberFormat:
    def test_starts_with_the_biller_code(self):
        number = _generate()
        assert number.startswith(SimulatedOneLinkProvider.BILLER_CODE)

    def test_is_entirely_numeric(self):
        """Real 1Bill consumer numbers are all-numeric — this must stay
        true regardless of which RNG produces the digits. Checked across
        many samples: the bug this guards against (a uuid4 hex suffix
        that can contain A-F) would only show up in roughly 1 - (10/16)^4
        (~85%) of individual samples, so a single sample isn't a
        reliable regression test on its own."""
        for _ in range(30):
            number = _generate()
            assert number.isdigit(), number

    def test_check_digit_is_internally_consistent(self):
        provider = SimulatedOneLinkProvider()
        number = _generate()
        # BILLER_CODE (4 chars) + body (12 digits) + 1 check digit.
        body_digits = number[len(SimulatedOneLinkProvider.BILLER_CODE):-1]
        expected_check = provider._check_digit(body_digits)
        assert number[-1] == expected_check

    def test_many_calls_produce_distinct_numbers(self):
        """Not a statistical randomness test — just confirms the
        generator isn't accidentally deterministic (e.g. a fixed seed)
        after the RNG swap."""
        numbers = {_generate() for _ in range(50)}
        assert len(numbers) == 50