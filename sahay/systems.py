"""Mock Paytm back-office systems.

These stand in for the real ledger, UPI/NPCI switch, recharge operators,
merchant settlements, refund engine and CRM. Agents only touch the world
through these methods, and every write is recorded in the audit trail.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .store import Store

REFUND_ETA_HOURS = 24          # UPI reversal / wallet refund credit
REFUND_SLA_DAYS = 5            # RBI TAT for failed-txn refunds (T+5)
BANK_TICKET_ETA_HOURS = 24     # UDIR / beneficiary bank resolution
SETTLEMENT_REPUSH_ETA_HOURS = 4


class PaytmSystems:
    def __init__(self, store: Store):
        self.s = store

    # ---------- audit ----------
    def _audit(self, actor: str, action: str, target: str, **details) -> dict:
        entry = {"ts": self.s.ts(), "actor": actor, "action": action,
                 "target": target, "details": details}
        self.s.audit.append(entry)
        return entry

    # ---------- read APIs ----------
    def get_customer(self, customer_id: str) -> dict | None:
        return self.s.customers.get(customer_id)

    def ledger_lookup(self, txn_id: str) -> dict | None:
        return self.s.txns.get(txn_id)

    def ledger_recent(self, customer_id: str, amount: float | None = None,
                      hours: int = 240) -> list[dict]:
        since = self.s.now - timedelta(hours=hours)
        out = []
        for t in self.s.txns.values():
            if t["customer_id"] != customer_id:
                continue
            if datetime.fromisoformat(t["ts"]) < since:
                continue
            if amount is not None and abs(t["amount"] - amount) > 0.5:
                continue
            out.append(t)
        return sorted(out, key=lambda t: t["ts"], reverse=True)

    def upi_status(self, rrn: str) -> dict | None:
        return self.s.npci.get(rrn)

    def recharge_status(self, txn_id: str) -> dict | None:
        return self.s.recharges.get(txn_id)

    def settlement_status(self, txn_id: str) -> dict | None:
        return self.s.settlements.get(txn_id)

    def refunds_for_txn(self, txn_id: str) -> list[dict]:
        return [r for r in self.s.refunds.values() if r["txn_id"] == txn_id]

    def dispute_history(self, customer_id: str, days: int = 30) -> int:
        c = self.s.customers.get(customer_id) or {}
        since = self.s.now - timedelta(days=days)
        live = sum(1 for k in self.s.cases.values()
                   if k["customer_id"] == customer_id
                   and datetime.fromisoformat(k["created_at"]) >= since)
        return c.get("prior_disputes_30d", 0) + max(0, live - 1)

    # ---------- write APIs ----------
    def initiate_refund(self, txn_id: str, amount: float, reason: str, actor: str) -> dict:
        rid = self.s.next_id("RF")
        r = {"refund_id": rid, "txn_id": txn_id, "amount": amount, "status": "INITIATED",
             "reason": reason, "created_at": self.s.ts(),
             "eta": (self.s.now + timedelta(hours=REFUND_ETA_HOURS)).isoformat(timespec="minutes")}
        self.s.refunds[rid] = r
        self._audit(actor, "REFUND_INITIATED", txn_id, refund_id=rid, amount=amount, reason=reason)
        return r

    def retrigger_refund(self, refund_id: str, actor: str) -> dict:
        r = self.s.refunds[refund_id]
        r["status"] = "INITIATED"
        r["retriggered_at"] = self.s.ts()
        r["eta"] = (self.s.now + timedelta(hours=REFUND_ETA_HOURS)).isoformat(timespec="minutes")
        self._audit(actor, "REFUND_RETRIGGERED", r["txn_id"], refund_id=refund_id)
        return r

    def raise_bank_ticket(self, txn_id: str, bank: str, reason: str, actor: str) -> dict:
        tid = self.s.next_id("BT")
        t = {"ticket_id": tid, "kind": "BANK", "txn_id": txn_id, "bank": bank, "reason": reason,
             "status": "OPEN", "created_at": self.s.ts(),
             "eta": (self.s.now + timedelta(hours=BANK_TICKET_ETA_HOURS)).isoformat(timespec="minutes")}
        self.s.tickets[tid] = t
        self._audit(actor, "BANK_TICKET_RAISED", txn_id, ticket_id=tid, bank=bank, reason=reason)
        return t

    def repush_settlement(self, txn_id: str, actor: str) -> dict:
        st = self.s.settlements[txn_id]
        st["status"] = "REPUSHED"
        st["expected_at"] = (self.s.now + timedelta(hours=SETTLEMENT_REPUSH_ETA_HOURS)).isoformat(timespec="minutes")
        self._audit(actor, "SETTLEMENT_REPUSHED", txn_id, merchant_id=st["merchant_id"])
        return st

    def create_crm_ticket(self, case_id: str, queue: str, summary: str, actor: str) -> dict:
        tid = self.s.next_id("CRM")
        t = {"ticket_id": tid, "kind": "CRM", "case_id": case_id, "queue": queue,
             "summary": summary, "status": "OPEN", "created_at": self.s.ts()}
        self.s.tickets[tid] = t
        self._audit(actor, "CRM_TICKET_CREATED", case_id, ticket_id=tid, queue=queue)
        return t

    def send_message(self, to: str, channel: str, text: str, actor: str, case_id: str = "") -> dict:
        m = {"ts": self.s.ts(), "to": to, "channel": channel, "text": text, "case_id": case_id}
        self.s.messages.append(m)
        self._audit(actor, "MESSAGE_SENT", to, channel=channel, case_id=case_id)
        return m

    # ---------- world simulation ----------
    def tick(self) -> list[str]:
        """Mature pending operations whose ETA has passed. Returns change log."""
        now = self.s.now
        changes = []
        for r in self.s.refunds.values():
            if r["status"] == "INITIATED" and datetime.fromisoformat(r["eta"]) <= now:
                r["status"] = "COMPLETED"
                r["completed_at"] = self.s.ts()
                changes.append(f"Refund {r['refund_id']} completed")
        for t in self.s.tickets.values():
            if t["kind"] == "BANK" and t["status"] == "OPEN" and datetime.fromisoformat(t["eta"]) <= now:
                t["status"] = "RESOLVED"
                txn = self.s.txns.get(t["txn_id"])
                # Deemed/pending UPI resolves: beneficiary bank confirms credit or reverses.
                if txn and txn.get("rrn") in self.s.npci:
                    n = self.s.npci[txn["rrn"]]
                    n["status"] = n.get("resolves_to", "SUCCESS")
                    t["resolution"] = n["status"]
                changes.append(f"Bank ticket {t['ticket_id']} resolved")
        for st in self.s.settlements.values():
            if st["status"] == "REPUSHED" and datetime.fromisoformat(st["expected_at"]) <= now:
                st["status"] = "SETTLED"
                st["utr"] = st.get("utr") or "UTR" + st["txn_id"].lstrip("T")[-10:].rjust(10, "0")
                changes.append(f"Settlement for {st['txn_id']} credited")
        return changes
