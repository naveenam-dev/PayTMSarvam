"""Policy & risk rules. Every proposed action is checked here before it runs.

POLICY_KB is the policy knowledge base the agent cites. In production this is
RAG over policy documents (pgvector); here it is a small in-memory corpus with
keyword retrieval so the demo runs offline.
"""
from __future__ import annotations

import re

AUTO_REFUND_LIMIT = 5000        # ₹ — above this a human must approve money movement
SETTLEMENT_AUTO_LIMIT = 50000   # ₹
RISK_THRESHOLD = 0.7
MAX_DISPUTES_30D = 3
MIN_CONFIDENCE = 0.8

MONEY_ACTIONS = {"REFUND", "RETRIGGER_REFUND", "REPUSH_SETTLEMENT"}

POLICY_KB = [
    {"id": "POL-01", "title": "Auto-refund limit",
     "text": f"Refunds and reversals up to ₹{AUTO_REFUND_LIMIT:,} for a failed transaction may be issued automatically. Higher amounts need approval by a disputes officer."},
    {"id": "POL-02", "title": "Risk score",
     "text": f"No automatic money movement for accounts with a fraud risk score of {RISK_THRESHOLD} or more. Send to the risk team."},
    {"id": "POL-03", "title": "Dispute velocity",
     "text": f"Accounts with {MAX_DISPUTES_30D} or more disputes in 30 days need manual review before any refund."},
    {"id": "POL-04", "title": "No double refund",
     "text": "The total refunded for a transaction must never exceed the transaction amount. If a refund is already completed, share its reference instead."},
    {"id": "POL-05", "title": "Ownership",
     "text": "Act only on transactions that belong to the customer who raised the dispute."},
    {"id": "POL-06", "title": "Confidence",
     "text": f"Automatic actions need an investigation confidence of {MIN_CONFIDENCE} or more. Otherwise ask the customer or escalate."},
    {"id": "POL-07", "title": "Failed UPI TAT",
     "text": "RBI TAT harmonisation: a failed UPI debit must be reversed by T+1. A delayed reversal is eligible for refund."},
    {"id": "POL-08", "title": "Deemed / pending UPI",
     "text": "For deemed or pending UPI transactions do not refund. Raise a UDIR ticket with the beneficiary bank and follow up after T+1."},
    {"id": "POL-09", "title": "Refund SLA",
     "text": "A refund not credited within T+5 is stuck. Re-trigger it once automatically and follow up."},
    {"id": "POL-10", "title": "Merchant settlement",
     "text": f"Merchant settlements are T+1. A delayed settlement up to ₹{SETTLEMENT_AUTO_LIMIT:,} may be re-pushed automatically."},
]


def retrieve(query: str, k: int = 2) -> list[dict]:
    q = set(re.findall(r"[a-z]+", query.lower()))
    scored = []
    for doc in POLICY_KB:
        words = set(re.findall(r"[a-z]+", (doc["title"] + " " + doc["text"]).lower()))
        scored.append((len(q & words), doc))
    scored.sort(key=lambda x: -x[0])
    return [d for score, d in scored[:k] if score > 0]


def check_action(action: dict, ctx: dict) -> dict:
    """Return {"decision": "ALLOW"|"HUMAN", "checks": [...], "cited": [...]}."""
    checks = []

    def rule(pid, ok, detail):
        checks.append({"policy": pid, "pass": bool(ok), "detail": detail})

    typ = action["type"]
    cust, txn = ctx["customer"], ctx.get("txn") or {}
    amt = action.get("amount", 0)

    rule("POL-05", txn.get("customer_id") == cust["customer_id"],
         f"txn owner {txn.get('customer_id')} vs claimant {cust['customer_id']}")

    if typ in MONEY_ACTIONS:
        limit = SETTLEMENT_AUTO_LIMIT if typ == "REPUSH_SETTLEMENT" else AUTO_REFUND_LIMIT
        rule("POL-01" if typ != "REPUSH_SETTLEMENT" else "POL-10", amt <= limit,
             f"₹{amt:,.0f} vs auto limit ₹{limit:,}")
        if cust.get("kind") != "merchant":
            rule("POL-02", cust["risk_score"] < RISK_THRESHOLD,
                 f"risk score {cust['risk_score']} (threshold {RISK_THRESHOLD})")
            rule("POL-03", ctx["disputes_30d"] < MAX_DISPUTES_30D,
                 f"{ctx['disputes_30d']} disputes in 30 days (max {MAX_DISPUTES_30D - 1})")
        if typ == "REFUND":
            already = sum(r["amount"] for r in ctx.get("refunds", []) if r["status"] != "FAILED")
            rule("POL-04", already + amt <= txn.get("amount", 0),
                 f"already refunded ₹{already:,.0f} + ₹{amt:,.0f} ≤ ₹{txn.get('amount', 0):,.0f}")
        rule("POL-06", ctx["confidence"] >= MIN_CONFIDENCE,
             f"confidence {ctx['confidence']:.2f} (min {MIN_CONFIDENCE})")

    ok = all(c["pass"] for c in checks)
    cited = [c["policy"] for c in checks if not c["pass"]] or [c["policy"] for c in checks]
    return {"decision": "ALLOW" if ok else "HUMAN", "checks": checks, "cited": cited}


def policy_text(pid: str) -> str:
    for d in POLICY_KB:
        if d["id"] == pid:
            return f"{d['id']} {d['title']}: {d['text']}"
    return pid
