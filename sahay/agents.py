"""Sahay AI agents.

Orchestrator  - keeps case state, runs the steps below, handles follow-ups.
Intake        - language, issue type, txn ID, amount (rules; LLM if configured).
Investigator  - calls ledger / UPI / recharge / settlement / refund systems, finds root cause.
Policy & Risk - checks every proposed action against policy; ALLOW or HUMAN.
Action        - executes allowed actions and tells the customer the ETA.
Escalation    - writes a case brief for a human and holds the actions for approval.

Every step is appended to case["timeline"] (the reasoning trail shown in the
console) and every system write lands in the audit log.
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from . import llm, policy, sarvam
from .lang import LANG_NAMES, detect_language, has_template, pretty_ts, reply
from .systems import REFUND_SLA_DAYS, PaytmSystems
from .store import Store

Emit = Callable[[dict], Awaitable[None]]

ISSUE_LABEL = {"deducted": "Money deducted, not received", "recharge": "Failed recharge",
               "refund": "Pending refund", "merchant": "Merchant did not get payment", "other": "Other"}
ROOT_CAUSE_POLICY = {"UPI_FAILED_NO_REVERSAL": "POL-07", "UPI_PENDING_AT_BANK": "POL-08",
                     "REFUND_STUCK": "POL-09", "ALREADY_REFUNDED": "POL-04",
                     "SETTLEMENT_DELAYED": "POL-10", "OWNERSHIP_MISMATCH": "POL-05",
                     "AMBIGUOUS_TXN": "POL-06"}
TXN_RE = re.compile(r"\bT\d{8,}\b")
SPOKEN_TXN_RE = re.compile(r"(?<![A-Za-z0-9])(?:T|t|टी|ಟಿ)\s*[-.]?\s*((?:\d[\s-]*){8,}\d)")
AMT_RE = re.compile(r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)|([\d,]+(?:\.\d+)?)\s*(?:₹|rs\b|rupees?|rupaye|रुपये|रुपए)", re.I)


class Sahay:
    def __init__(self, store: Store, emit: Emit | None = None, step_delay: float = 0.0,
                 use_llm: bool = True, use_sarvam: bool = True):
        self.s = store
        self.sys = PaytmSystems(store)
        self.emit = emit
        self.delay = step_delay
        self._use_llm, self._use_sarvam = use_llm, use_sarvam

    # Checked per call, so keys set while the server runs take effect.
    @property
    def use_llm(self) -> bool:
        return self._use_llm and llm.available()

    @property
    def use_sarvam(self) -> bool:
        return self._use_sarvam and sarvam.available()

    # ================================================================ plumbing
    async def _push(self, case: dict) -> None:
        if self.emit:
            await self.emit({"type": "case", "case": case})

    async def _step(self, case, agent, title, detail="", data=None, kind="info"):
        case["timeline"].append({"ts": self.s.ts(), "agent": agent, "title": title,
                                 "detail": detail, "data": data or {}, "kind": kind})
        # On a live call the customer is waiting on the line, so keep the demo pause short.
        delay = 0 if case["channel"] == "call" else self.delay
        if delay:
            await asyncio.sleep(delay)
            case["_paused"] = case.get("_paused", 0) + delay
        await self._push(case)

    async def _say(self, case, text: str) -> None:
        cust = self.s.customers[case["customer_id"]]
        case["messages"].append({"from": "sahay", "ts": self.s.ts(), "text": text})
        self.sys.send_message(cust["phone"], case["channel"], text, "agent:action", case["case_id"])

    async def _localize(self, key: str, lang: str, **kw) -> str:
        """Reply in the customer's language: built-in template, else Sarvam translation, else English."""
        if has_template(key, lang) or lang == "en" or not self.use_sarvam:
            return reply(key, lang, **kw)
        english = reply(key, "en", **kw)
        return await sarvam.translate(english, lang, "en") or english
    # ================================================================ entry points
    async def open_case(self, customer_id: str, message: str, channel: str = "chat",
                        language: str | None = None) -> dict:
        cid = self.s.next_id("CASE-")
        case = {"case_id": cid, "customer_id": customer_id, "channel": channel,
                "created_at": self.s.ts(), "status": "NEW", "language": None, "issue": None,
                "txn_id": None, "amount": None, "root_cause": None, "confidence": 0.0,
                "plan": [], "policy": [], "pending_actions": [], "brief": None,
                "spoken_language": language, "text_en": None, "ai_summary": None,
                "followup_at": None, "followups": 0, "resolved_by": None, "resolved_at": None,
                "handle_ms": None, "messages": [{"from": "customer", "ts": self.s.ts(), "text": message, "channel": channel}],
                "timeline": []}
        self.s.cases[cid] = case
        await self._push(case)
        await self.run(case)
        return case

    async def customer_reply(self, case_id: str, text: str) -> dict:
        case = self.s.cases[case_id]
        case["messages"].append({"from": "customer", "ts": self.s.ts(), "text": text})
        if case["status"] in ("NEEDS_INFO", "NEW"):
            case["plan"], case["policy"] = [], []
            await self.run(case)
        else:
            await self._push(case)
        return case

    async def run(self, case: dict) -> None:
        t0 = time.perf_counter()
        case["_paused"] = 0.0
        await self.intake(case)
        ctx = await self.investigate(case)
        if case["status"] != "NEEDS_INFO":
            decisions = await self.decide(case, ctx)
            if case.get("force_escalate") or any(d["decision"] == "HUMAN" for d in decisions):
                await self.escalate(case, ctx, decisions)
            else:
                await self.act(case, ctx, case["plan"], actor="agent:action")
        # Agent time only: excludes the demo's deliberate pause between steps.
        case["handle_ms"] = round((time.perf_counter() - t0 - case.pop("_paused", 0)) * 1000, 1)
        await self._push(case)

    # ================================================================ 1. intake
    async def intake(self, case: dict) -> None:
        case["status"] = "UNDERSTANDING"
        text = " ".join(m["text"] for m in case["messages"] if m["from"] == "customer")
        lang = case.get("spoken_language") or detect_language(case["messages"][0]["text"])
        if lang == "en":
            # Sarvam STT often returns Hindi speech as romanised Hinglish tagged en-IN; reply in Hindi then.
            lang = detect_language(case["messages"][0]["text"])
        notes = []
        if case["channel"] in ("voice", "call"):
            notes.append(f"{'live call' if case['channel'] == 'call' else 'voice note'} transcribed by Sarvam {sarvam.STT_MODEL}")
        # Spoken IDs arrive as "T 2610 000 001" or "टी 2610..."; join them back up.
        text = SPOKEN_TXN_RE.sub(lambda m: "T" + re.sub(r"\D", "", m.group(1)), text)
        # Languages without built-in keywords: Sarvam translates to English for the rules path.
        if lang not in ("en", "hi", "kn") and self.use_sarvam and not case.get("text_en"):
            case["text_en"] = await sarvam.translate(text, "en", None)
            if case["text_en"]:
                notes.append(f"Sarvam translation: \"{case['text_en'][:120]}\"")
        if case.get("text_en"):
            text = text + " " + case["text_en"]
        txns = TXN_RE.findall(text)
        amt = None
        for m in AMT_RE.finditer(text):
            amt = float((m.group(1) or m.group(2)).replace(",", ""))
            break
        if amt is None:  # bare number not part of a txn ID
            nums = [n for n in re.findall(r"\b\d{2,6}\b", TXN_RE.sub(" ", text))]
            amt = float(nums[0]) if nums else None
        low = text.lower()
        cust = self.s.customers.get(case["customer_id"], {})
        if re.search(r"recharge|रिचार्ज|ರೀಚಾರ್ಜ್", low):
            issue = "recharge"
        elif re.search(r"deduct|debited|kat gaye|कट|ಕಡಿತ", low) and not re.search(r"refund\W+(?:\w+\W+){0,4}(?:pending|not\s+(?:received|credited))", low):
            issue = "deducted"
        elif re.search(r"refund|रिफंड|ರಿಫಂಡ್", low):
            issue = "refund"
        elif cust.get("kind") == "merchant" or re.search(r"settlement|सेटलमेंट|ಸೆಟಲ್ಮೆಂಟ್", low):
            issue = "merchant"
        else:
            issue = "deducted"
        source = "rules"
        if self.use_llm:
            got = await llm.extract_intake(text)
            if got:
                source = llm.mode()
                if got.get("summary_en"):
                    notes.append(f"{llm.mode()}: \"{got['summary_en']}\"")
                issue = got.get("issue") if got.get("issue") in ISSUE_LABEL else issue
                amt = amt or got.get("amount")
                if not txns and got.get("txn_id"):
                    txns = [got["txn_id"]]
        case.update(language=lang, issue=issue, txn_id=txns[-1] if txns else None, amount=amt)
        await self._step(case, "Intake", "Understood the complaint",
                         f"{LANG_NAMES.get(lang, lang)} · {ISSUE_LABEL[issue]} · "
                         f"txn {case['txn_id'] or 'not given'} · amount {('₹%s' % f'{amt:,.0f}') if amt else 'not given'}"
                         + "".join(f"\n{n}" for n in notes) + f"\nextraction: {source}",
                         {"extraction": source})

    # ================================================================ 2. investigate
    async def investigate(self, case: dict) -> dict:
        case["status"] = "INVESTIGATING"
        S = self.sys
        cust = S.get_customer(case["customer_id"])
        disputes = S.dispute_history(cust["customer_id"])
        await self._step(case, "Investigator", "CRM.get_customer",
                         f"{cust['name']} · KYC {cust['kyc']} · risk {cust['risk_score']} · {disputes} disputes/30d",
                         kind="tool")
        ctx = {"customer": cust, "disputes_30d": disputes, "txn": None, "refunds": [], "confidence": 0.0}

        txn = None
        if case["txn_id"]:
            txn = S.ledger_lookup(case["txn_id"])
            if not txn and case["channel"] in ("voice", "call"):
                # Spoken digits often come back with one digit dropped/added; match within the caller's own txns only.
                txn = _fuzzy_txn(case["txn_id"], S.ledger_recent(cust["customer_id"], hours=24 * 30))
                if txn:
                    await self._step(case, "Investigator", f"Matched spoken ID {case['txn_id']} → {txn['txn_id']}",
                                     "Speech-to-text was one digit off; matched against this customer's own transactions.",
                                     kind="tool")
                    case["txn_id"] = txn["txn_id"]
            await self._step(case, "Investigator", f"Ledger.lookup({case['txn_id']})",
                             _txn_line(txn) if txn else "not found", kind="tool")
            if txn and txn["customer_id"] != cust["customer_id"]:
                ctx.update(txn=txn, confidence=0.95)
                return await self._conclude(case, ctx, "OWNERSHIP_MISMATCH",
                                            "Txn belongs to a different account.", [], force=True)
            conf = 0.95
        if not txn:
            want = {"recharge": {"RECHARGE"}, "merchant": {"MERCHANT_COLLECT"},
                    "deducted": {"UPI_P2P", "UPI_P2M"}}.get(case["issue"])
            cands = [t for t in S.ledger_recent(cust["customer_id"], case["amount"])
                     if not want or t["type"] in want]
            await self._step(case, "Investigator",
                             f"Ledger.recent(amount={case['amount']}, type={case['issue']})",
                             f"{len(cands)} matching transaction(s) in last 10 days", kind="tool")
            if len(cands) != 1:
                key = "NEEDS_INFO" if cands else "NOT_FOUND"
                case.update(status="NEEDS_INFO", root_cause="AMBIGUOUS_TXN" if cands else "TXN_NOT_FOUND",
                            confidence=0.4)
                await self._step(case, "Investigator", "Cannot pick a transaction safely",
                                 "Asking the customer for the transaction ID instead of guessing.", kind="decision")
                await self._say(case, await self._localize(key, case["language"], name=_first(cust)))
                return ctx
            txn, conf = cands[0], 0.85
            case["txn_id"] = txn["txn_id"]
        ctx.update(txn=txn, confidence=conf)
        case["amount"] = txn["amount"]

        refunds = S.refunds_for_txn(txn["txn_id"])
        ctx["refunds"] = refunds
        await self._step(case, "Investigator", f"RefundEngine.for_txn({txn['txn_id']})",
                         ", ".join(f"{r['refund_id']} {r['status']} (since {pretty_ts(r['created_at'])})" for r in refunds)
                         or "no refunds", kind="tool")

        amt = txn["amount"]
        done = [r for r in refunds if r["status"] == "COMPLETED"]
        stuck = [r for r in refunds if r["status"] == "STUCK" or (
            r["status"] in ("INITIATED", "PROCESSING")
            and datetime.fromisoformat(r["created_at"]) < self.s.now - timedelta(days=REFUND_SLA_DAYS))]
        moving = [r for r in refunds if r["status"] in ("INITIATED", "PROCESSING")]

        if done:
            r = done[0]
            return await self._conclude(case, ctx, "ALREADY_REFUNDED",
                                        f"Refund {r['refund_id']} already completed on {pretty_ts(r['completed_at'])}.",
                                        [{"type": "NOTIFY", "key": "ALREADY_REFUNDED", "ref": r["refund_id"],
                                          "eta": r["completed_at"]}])
        if stuck:
            r = stuck[0]
            return await self._conclude(case, ctx, "REFUND_STUCK",
                                        f"Refund {r['refund_id']} has not moved for more than T+{REFUND_SLA_DAYS}.",
                                        [{"type": "RETRIGGER_REFUND", "refund_id": r["refund_id"], "amount": r["amount"]}])
        if moving:
            r = moving[0]
            eta = (datetime.fromisoformat(r["created_at"]) + timedelta(days=REFUND_SLA_DAYS)).isoformat(timespec="minutes")
            return await self._conclude(case, ctx, "REFUND_IN_PROGRESS", "Refund is moving and within SLA.",
                                        [{"type": "NOTIFY", "key": "REFUND_IN_PROGRESS", "ref": r["refund_id"], "eta": eta}])

        if txn["type"] in ("UPI_P2P", "UPI_P2M"):
            n = S.upi_status(txn["rrn"])
            await self._step(case, "Investigator", f"NPCI.upi_status(RRN {txn['rrn']})",
                             f"{n['status']} · response {n['response_code']} · reversal {'yes' if n['reversal'] else 'no'} · {n['beneficiary_bank']}",
                             kind="tool")
            if n["status"] == "FAILED" and not n["reversal"]:
                return await self._conclude(case, ctx, "UPI_FAILED_NO_REVERSAL",
                                            f"Debit happened, credit failed at {n['beneficiary_bank']} (U30), auto-reversal missing.",
                                            [{"type": "REFUND", "amount": amt}])
            if n["status"] in ("DEEMED", "PENDING"):
                return await self._conclude(case, ctx, "UPI_PENDING_AT_BANK",
                                            f"UPI is deemed/pending at {n['beneficiary_bank']}; refund not allowed yet.",
                                            [{"type": "BANK_TICKET", "bank": n["beneficiary_bank"]}])
            return await self._conclude(case, ctx, "UPI_SUCCESS_CREDITED",
                                        f"NPCI shows success; {n['beneficiary_bank']} credited {txn['payee']}.",
                                        [{"type": "NOTIFY", "key": "CREDITED_PROOF", "ref": txn["rrn"]}])

        if txn["type"] == "RECHARGE":
            r = S.recharge_status(txn["txn_id"])
            await self._step(case, "Investigator", f"Operator.recharge_status({r['operator']})",
                             f"{r['status']} · ref {r['operator_ref'] or '-'}", kind="tool")
            if r["status"] == "FAILED":
                return await self._conclude(case, ctx, "RECHARGE_FAILED",
                                            f"{r['operator']} rejected the recharge; wallet/bank was debited.",
                                            [{"type": "REFUND", "amount": amt}])
            return await self._conclude(case, ctx, "RECHARGE_SUCCESS", f"{r['operator']} confirms success.",
                                        [{"type": "NOTIFY", "key": "RECHARGE_DONE", "ref": r["operator_ref"]}])

        if txn["type"] == "MERCHANT_COLLECT":
            st = S.settlement_status(txn["txn_id"])
            await self._step(case, "Investigator", f"Settlements.status({txn['txn_id']})",
                             f"{st['status']} · expected by {pretty_ts(st['expected_at'])}", kind="tool")
            if st["status"] == "SETTLED":
                return await self._conclude(case, ctx, "SETTLED", "Settlement already credited.",
                                            [{"type": "NOTIFY", "key": "SETTLED", "ref": st["utr"]}])
            if datetime.fromisoformat(st["expected_at"]) < self.s.now:
                return await self._conclude(case, ctx, "SETTLEMENT_DELAYED", "Settlement missed the T+1 batch.",
                                            [{"type": "REPUSH_SETTLEMENT", "amount": amt}])
            return await self._conclude(case, ctx, "SETTLEMENT_IN_WINDOW", "Settlement is within T+1.",
                                        [{"type": "NOTIFY", "key": "SETTLEMENT_IN_WINDOW", "eta": st["expected_at"]}])

        return await self._conclude(case, ctx, "UNKNOWN", "Could not classify.", [], force=True)

    async def _conclude(self, case, ctx, cause, why, plan, force=False) -> dict:
        case.update(root_cause=cause, confidence=ctx["confidence"], plan=plan)
        if force:
            case["force_escalate"] = True
        pid = ROOT_CAUSE_POLICY.get(cause)
        await self._step(case, "Investigator", f"Root cause: {cause.replace('_', ' ').title().replace('Upi', 'UPI')}",
                         why + (f"  [{policy.policy_text(pid)}]" if pid else ""),
                         {"confidence": ctx["confidence"], "plan": [p["type"] for p in plan]}, kind="decision")
        return ctx

    # ================================================================ 3. decide
    async def decide(self, case: dict, ctx: dict) -> list[dict]:
        case["status"] = "CHECKING_POLICY"
        out = []
        for a in case["plan"]:
            res = policy.check_action(a, ctx)
            res["action"] = a["type"]
            out.append(res)
            failed = [c for c in res["checks"] if not c["pass"]]
            detail = "; ".join(f"{c['policy']} ✗ {c['detail']}" for c in failed) or \
                     "; ".join(f"{c['policy']} ✓ {c['detail']}" for c in res["checks"]) or "no money movement"
            await self._step(case, "Policy & Risk", f"{a['type']} → {res['decision']}", detail,
                             {"checks": res["checks"]}, kind="ok" if res["decision"] == "ALLOW" else "warn")
        case["policy"] = out
        return out

    # ================================================================ 4. act
    async def act(self, case: dict, ctx: dict, plan: list[dict], actor: str) -> None:
        case["status"] = "ACTING"
        S, cust, txn = self.sys, ctx["customer"], ctx["txn"]
        lang, name = case["language"], _first(cust)
        base = dict(name=name, amt=txn["amount"], txn=txn["txn_id"], bank=txn.get("payee_bank", ""),
                    payee=txn.get("payee", ""), operator=txn.get("operator", ""), case=case["case_id"])
        followup = None
        for a in plan:
            t = a["type"]
            if t == "REFUND":
                r = S.initiate_refund(txn["txn_id"], a["amount"], case["root_cause"], actor)
                await self._step(case, "Action", "RefundEngine.initiate_refund",
                                 f"{r['refund_id']} for ₹{a['amount']:,.0f}, ETA {pretty_ts(r['eta'])}", kind="action")
                key = "RECHARGE_REFUND" if txn["type"] == "RECHARGE" else "REFUND_INITIATED"
                await self._say(case, await self._localize(key, lang, **base, ref=r["refund_id"], eta=r["eta"]))
                followup = r["eta"]
            elif t == "RETRIGGER_REFUND":
                r = S.retrigger_refund(a["refund_id"], actor)
                await self._step(case, "Action", "RefundEngine.retrigger",
                                 f"{r['refund_id']} re-sent, ETA {pretty_ts(r['eta'])}", kind="action")
                await self._say(case, await self._localize("REFUND_RETRIGGERED", lang, **base, ref=r["refund_id"], eta=r["eta"]))
                followup = r["eta"]
            elif t == "BANK_TICKET":
                tk = S.raise_bank_ticket(txn["txn_id"], a["bank"], "UPI deemed/pending - UDIR", actor)
                await self._step(case, "Action", "Bank.raise_udir_ticket",
                                 f"{tk['ticket_id']} with {a['bank']}, ETA {pretty_ts(tk['eta'])}", kind="action")
                await self._say(case, await self._localize("BANK_TICKET", lang, **base, ref=tk["ticket_id"], eta=tk["eta"]))
                followup = tk["eta"]
            elif t == "REPUSH_SETTLEMENT":
                st = S.repush_settlement(txn["txn_id"], actor)
                await self._step(case, "Action", "Settlements.repush",
                                 f"re-pushed to bank, ETA {pretty_ts(st['expected_at'])}", kind="action")
                await self._say(case, await self._localize("SETTLEMENT_REPUSHED", lang, **base, eta=st["expected_at"]))
                followup = st["expected_at"]
            elif t == "NOTIFY":
                await self._step(case, "Action", f"Notify customer ({a['key'].replace('_', ' ').lower()})",
                                 "No money movement needed; sharing proof / status.", kind="action")
                await self._say(case, await self._localize(a["key"], lang, **base, ref=a.get("ref"), eta=a.get("eta")))
        case["resolved_by"] = case["resolved_by"] or ("HUMAN" if actor.startswith("human") else "AI")
        if followup:
            case.update(status="AWAITING_FOLLOWUP", followup_at=followup)
            await self._step(case, "Orchestrator", "Follow-up scheduled",
                             f"Will verify the outcome at {pretty_ts(followup)} and close the case.")
        else:
            case.update(status="RESOLVED", resolved_at=self.s.ts())
            await self._step(case, "Orchestrator", "Case resolved", "Customer has the answer and proof.", kind="ok")

    # ================================================================ 5. escalate
    async def escalate(self, case: dict, ctx: dict, decisions: list[dict]) -> None:
        cust, txn = ctx["customer"], ctx["txn"] or {}
        blocked = [c for d in decisions for c in d["checks"] if not c["pass"]]
        risk_flags = [c["policy"] for c in blocked if c["policy"] in ("POL-02", "POL-03", "POL-05", "POL-04")]
        if case["root_cause"] == "OWNERSHIP_MISMATCH":
            risk_flags.append("POL-05")
            blocked.append({"policy": "POL-05", "pass": False,
                            "detail": f"txn owner {txn.get('customer_id')} vs claimant {cust['customer_id']}"})
        if risk_flags:
            rec = "Review account activity first. Approve only if the claim checks out; otherwise reject."
            queue = "Risk team"
        else:
            rec = "Evidence is clear. Approve the proposed action."
            queue = "Disputes L2"
        proposed = ", ".join(f"{p['type']}" + (f" ₹{p['amount']:,.0f}" if p.get("amount") else "") for p in case["plan"]) or "none"
        case["brief"] = {
            "headline": f"{ISSUE_LABEL[case['issue']]} · ₹{(txn.get('amount') or case['amount'] or 0):,.0f} · {cust['name']}",
            "customer": f"{cust['name']} ({cust['customer_id']}), KYC {cust['kyc']}, risk {cust['risk_score']}, "
                        f"{ctx['disputes_30d']} disputes/30d, account {cust['account_age_days']} days",
            "transaction": _txn_line(txn) if txn else "-",
            "evidence": [s["title"] + ": " + s["detail"] for s in case["timeline"] if s["kind"] == "tool"],
            "root_cause": case["root_cause"],
            "confidence": case["confidence"],
            "proposed_action": proposed,
            "why_human": [policy.policy_text(c["policy"]) + f" — {c['detail']}" for c in blocked],
            "risk_flags": sorted(set(risk_flags)),
            "recommendation": rec,
            "queue": queue,
        }
        if self.use_llm:
            case["ai_summary"] = await llm.officer_summary(case["brief"])
            if case["ai_summary"]:
                case["brief"]["ai_summary"] = case["ai_summary"]
                await self._step(case, "Escalation", f"Officer summary written by {llm.mode()}",
                                 case["ai_summary"])
        case["pending_actions"] = case["plan"]
        tk = self.sys.create_crm_ticket(case["case_id"], queue, case["brief"]["headline"], "agent:escalation")
        case["brief"]["ticket"] = tk["ticket_id"]
        case["status"] = "ESCALATED"
        await self._step(case, "Escalation", f"Escalated to {queue}",
                         f"{tk['ticket_id']} · proposed: {proposed} · {rec}", kind="warn")
        await self._say(case, await self._localize("ESCALATED", case["language"], name=_first(cust),
                                    amt=txn.get("amount") or case["amount"] or 0, txn=txn.get("txn_id", "-")))

    # ================================================================ human decisions
    async def human_decision(self, case_id: str, approve: bool, officer: str, note: str = "") -> dict:
        case = self.s.cases[case_id]
        if case["status"] != "ESCALATED":
            return case
        cust = self.s.customers[case["customer_id"]]
        txn = self.sys.ledger_lookup(case["txn_id"]) if case["txn_id"] else None
        self.sys._audit(f"human:{officer}", "APPROVED" if approve else "REJECTED", case_id, note=note)
        await self._step(case, "Human", f"{officer} {'approved' if approve else 'rejected'}", note or "-",
                         kind="ok" if approve else "warn")
        if approve and txn:
            ctx = {"customer": cust, "txn": txn, "refunds": self.sys.refunds_for_txn(txn["txn_id"]),
                   "disputes_30d": 0, "confidence": case["confidence"]}
            await self.act(case, ctx, case["pending_actions"], actor=f"human:{officer}")
            case["resolved_by"] = "HUMAN"
        else:
            case.update(status="REJECTED", resolved_by="HUMAN", resolved_at=self.s.ts())
            await self._say(case, await self._localize("REJECTED", case["language"], name=_first(cust),
                                        txn=case["txn_id"] or "-", reason=note or "the claim could not be verified"))
        case["pending_actions"] = []
        await self._push(case)
        return case

    # ================================================================ 6. follow up
    async def run_followups(self) -> list[str]:
        log = self.sys.tick()
        for case in list(self.s.cases.values()):
            if case["status"] != "AWAITING_FOLLOWUP" or datetime.fromisoformat(case["followup_at"]) > self.s.now:
                continue
            case["followups"] += 1
            cust = self.s.customers[case["customer_id"]]
            txn = self.sys.ledger_lookup(case["txn_id"])
            base = dict(name=_first(cust), amt=txn["amount"], txn=txn["txn_id"], case=case["case_id"],
                        bank=txn.get("payee_bank", ""), payee=txn.get("payee", ""))
            refunds = self.sys.refunds_for_txn(txn["txn_id"])
            tickets = [t for t in self.s.tickets.values() if t.get("txn_id") == txn["txn_id"] and t["kind"] == "BANK"]
            st = self.sys.settlement_status(txn["txn_id"])
            done, key = False, "RESOLVED"
            if any(r["status"] == "COMPLETED" for r in refunds):
                done = True
                detail = "Refund credited."
            elif st and st["status"] == "SETTLED":
                done, key, base["ref"] = True, "SETTLED", st["utr"]
                detail = f"Settlement credited, UTR {st['utr']}."
            elif tickets and tickets[-1]["status"] == "RESOLVED":
                outcome = tickets[-1].get("resolution")
                if outcome == "SUCCESS":
                    done, key, base["ref"] = True, "BANK_CONFIRMED_SUCCESS", txn["rrn"]
                    detail = "Bank confirmed credit to beneficiary."
                else:
                    await self._step(case, "Orchestrator", "Follow-up: bank marked txn FAILED",
                                     "Re-investigating; refund now applies.", kind="decision")
                    case.update(status="NEW", plan=[], policy=[])
                    ctx = await self.investigate(case)
                    decisions = await self.decide(case, ctx)
                    if any(d["decision"] == "HUMAN" for d in decisions):
                        await self.escalate(case, ctx, decisions)
                    else:
                        await self.act(case, ctx, case["plan"], actor="agent:action")
                    continue
            if done:
                await self._step(case, "Orchestrator", "Follow-up: outcome verified", detail, kind="ok")
                await self._say(case, await self._localize(key, case["language"], **base))
                case.update(status="RESOLVED", resolved_at=self.s.ts())
            elif case["followups"] >= 2:
                await self._step(case, "Orchestrator", "Follow-up: still not done after 2 checks",
                                 "Escalating to a human.", kind="warn")
                ctx = {"customer": cust, "txn": txn, "disputes_30d": 0, "confidence": case["confidence"]}
                case["plan"] = []
                await self.escalate(case, ctx, [])
            else:
                case["followup_at"] = (self.s.now + timedelta(hours=12)).isoformat(timespec="minutes")
                await self._step(case, "Orchestrator", "Follow-up: not done yet",
                                 f"Checking again at {pretty_ts(case['followup_at'])}.")
            await self._push(case)
        return log


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _fuzzy_txn(spoken_id: str, txns: list[dict]) -> dict | None:
    """Unique txn within edit distance 1 of the spoken ID, else unique match on the last 4 digits."""
    close = [t for t in txns if _edit_distance(spoken_id, t["txn_id"]) <= 1]
    if len(close) == 1:
        return close[0]
    tail = [t for t in txns if t["txn_id"][-4:] == spoken_id[-4:]]
    return tail[0] if len(tail) == 1 else None


def _first(cust: dict) -> str:
    return cust["name"].split()[0] if cust.get("kind") != "merchant" else cust["name"].split(" (")[0]


def _txn_line(t: dict) -> str:
    return (f"{t['txn_id']} · {t['type']} · ₹{t['amount']:,.0f} · {pretty_ts(t['ts'])} · "
            f"{t['ledger_status']} · to {t.get('payee', '-')}")
