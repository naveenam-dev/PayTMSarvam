"""In-memory state for the mock Paytm systems and Sahay AI cases.

One Store instance = one isolated "world". The live demo uses a global Store;
the 100-dispute simulation creates its own so it never touches demo data.
"""
from __future__ import annotations

import itertools
from datetime import datetime, timedelta


class Store:
    def __init__(self, now: datetime | None = None):
        # Simulated clock, so follow-ups (T+1, T+5 ...) can be demoed in minutes.
        self.now = now or datetime(2026, 10, 31, 11, 0)
        self.customers: dict[str, dict] = {}
        self.txns: dict[str, dict] = {}         # ledger, keyed by txn_id
        self.npci: dict[str, dict] = {}         # UPI switch status, keyed by rrn
        self.recharges: dict[str, dict] = {}    # operator status, keyed by txn_id
        self.settlements: dict[str, dict] = {}  # merchant settlement, keyed by txn_id
        self.refunds: dict[str, dict] = {}      # refund engine, keyed by refund_id
        self.tickets: dict[str, dict] = {}      # bank / CRM tickets
        self.messages: list[dict] = []          # outbound customer / merchant messages
        self.cases: dict[str, dict] = {}
        self.audit: list[dict] = []
        self._seq = itertools.count(1)

    def next_id(self, prefix: str) -> str:
        return f"{prefix}{next(self._seq):05d}"

    def advance(self, hours: float) -> None:
        self.now += timedelta(hours=hours)

    def ts(self) -> str:
        return self.now.isoformat(timespec="minutes")
