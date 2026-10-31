"""Metrics for the deck's impact slide, from a synthetic dispute run.

Ground truth comes from the generator (seed.generate_disputes) and is hidden
from the agents. Cost figures use the explicit assumptions below.
"""
from __future__ import annotations

import asyncio
from collections import Counter

from .agents import Sahay
from .seed import generate_disputes
from .store import Store

# Assumptions for the cost estimate. Change these to Paytm's real numbers.
HUMAN_COST_PER_TICKET = 45.0   # ₹: ~9 min of agent time at ~₹300/hour, loaded
AI_COST_PER_TICKET = 3.0       # ₹: LLM tokens + infra per case (estimate)
ESCALATED_HUMAN_SHARE = 0.6    # a human with a ready case brief needs ~60% of the usual time

AUTO_STATES = {"RESOLVED", "AWAITING_FOLLOWUP"}


def summarise(cases: list[dict]) -> dict:
    n = len(cases) or 1
    auto = [c for c in cases if c["status"] in AUTO_STATES and c["resolved_by"] == "AI"]
    esc = [c for c in cases if c["brief"]]
    info = [c for c in cases if c["status"] == "NEEDS_INFO"]
    times = [c["handle_ms"] for c in cases if c["handle_ms"] is not None]
    ai_cost = AI_COST_PER_TICKET * n + HUMAN_COST_PER_TICKET * ESCALATED_HUMAN_SHARE * len(esc)
    base_cost = HUMAN_COST_PER_TICKET * n
    return {
        "cases": len(cases),
        "auto_resolved": len(auto),
        "auto_resolution_rate": round(100 * len(auto) / n, 1),
        "escalated": len(esc),
        "escalation_rate": round(100 * len(esc) / n, 1),
        "needs_info": len(info),
        "avg_handle_ms": round(sum(times) / len(times), 1) if times else None,
        "cost_per_ticket_change_pct": round(100 * (ai_cost / base_cost - 1), 1),
        "root_causes": dict(Counter(c["root_cause"] for c in cases)),
    }


async def run_simulation(n: int = 100, seed: int = 7, use_llm: bool = False) -> dict:
    s = Store()
    disputes = generate_disputes(s, n, seed)
    agent = Sahay(s, emit=None, step_delay=0, use_llm=use_llm, use_sarvam=False)
    rows = []
    for d in disputes:
        case = await agent.open_case(d["customer_id"], d["message"])
        rows.append((d, case))

    cases = [c for _, c in rows]
    out = summarise(cases)

    expected_human = [c for d, c in rows if d["expected"]["outcome"] == "HUMAN"]
    escalated = [(d, c) for d, c in rows if c["brief"]]
    tp = sum(1 for d, _ in escalated if d["expected"]["outcome"] == "HUMAN")
    rc_ok = sum(1 for d, c in rows if d["expected"]["root_cause"] == c["root_cause"])
    missed = [d for d, c in rows if d["expected"]["outcome"] == "HUMAN" and not c["brief"]]
    out.update({
        "escalation_precision": round(100 * tp / len(escalated), 1) if escalated else None,
        "escalation_recall": round(100 * tp / len(expected_human), 1) if expected_human else None,
        "root_cause_accuracy": round(100 * rc_ok / len(rows), 1),
        "missed_escalations": [{"scenario": d["scenario"], "customer": d["customer_id"]} for d in missed],
        "false_escalations": [{"scenario": d["scenario"], "customer": d["customer_id"]}
                              for d, c in escalated if d["expected"]["outcome"] != "HUMAN"],
    })

    # Follow-up loop: move the clock forward and let the orchestrator verify outcomes.
    for _ in range(3):
        s.advance(24)
        await agent.run_followups()
    closed = [c for c in cases if c["status"] == "RESOLVED" and c["resolved_by"] == "AI"]
    out["closed_by_ai_after_followup"] = len(closed)
    out["closed_by_ai_after_followup_rate"] = round(100 * len(closed) / len(cases), 1)
    out["assumptions"] = {
        "human_cost_per_ticket_inr": HUMAN_COST_PER_TICKET,
        "ai_cost_per_ticket_inr": AI_COST_PER_TICKET,
        "escalated_human_share": ESCALATED_HUMAN_SHARE,
        "note": "Synthetic disputes; ground truth from generator, hidden from agents. "
                "Handle time is agent decision+action time; money reaching the customer depends on bank rails (T+1).",
    }
    out["scenarios"] = dict(Counter(d["scenario"] for d in disputes))
    return out


if __name__ == "__main__":
    import json
    import sys

    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    print(json.dumps(asyncio.run(run_simulation(n)), indent=2, ensure_ascii=False))
