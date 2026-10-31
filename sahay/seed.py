"""Seed data: scripted demo cases + a random dispute generator for the metrics run."""
from __future__ import annotations

import random
from datetime import timedelta

from .lang import complaint_text
from .store import Store

BANKS = ["SBI", "HDFC Bank", "ICICI Bank", "Axis Bank", "Canara Bank", "Kotak Bank", "Bank of Baroda"]
OPERATORS = ["Jio", "Airtel", "Vi", "BSNL"]


# ---------------------------------------------------------------- builders
def add_customer(s: Store, cid, name, phone, lang="en", risk=0.1, prior=0,
                 kind="consumer", true_fraud=False):
    s.customers[cid] = {"customer_id": cid, "name": name, "phone": phone, "language": lang,
                        "kind": kind, "kyc": "FULL", "risk_score": risk,
                        "prior_disputes_30d": prior, "account_age_days": 400,
                        "_true_fraud": true_fraud}  # hidden ground truth, never read by agents
    return cid


def _txn_id(s: Store) -> str:
    return "T2610" + s.next_id("")[-5:].rjust(6, "0")


def _rrn(s: Store) -> str:
    return "6304" + s.next_id("")[-5:].rjust(8, "0")


def _ts(s: Store, hours_ago: float) -> str:
    return (s.now - timedelta(hours=hours_ago)).isoformat(timespec="minutes")


def add_upi(s: Store, cid, amount, hours_ago, npci_status, payee="ravi@okaxis",
            bank="HDFC Bank", resolves_to="SUCCESS", p2m=False):
    tid, rrn = _txn_id(s), _rrn(s)
    s.txns[tid] = {"txn_id": tid, "customer_id": cid, "type": "UPI_P2M" if p2m else "UPI_P2P",
                   "amount": amount, "ts": _ts(s, hours_ago), "ledger_status": "DEBITED",
                   "rrn": rrn, "payee": payee, "payee_bank": bank}
    s.npci[rrn] = {"rrn": rrn, "status": npci_status, "reversal": False,
                   "response_code": {"FAILED": "U30", "DEEMED": "RB", "SUCCESS": "00"}.get(npci_status, "00"),
                   "beneficiary_bank": bank, "resolves_to": resolves_to}
    return tid


def add_recharge(s: Store, cid, amount, hours_ago, op_status, operator="Jio"):
    tid = _txn_id(s)
    s.txns[tid] = {"txn_id": tid, "customer_id": cid, "type": "RECHARGE", "amount": amount,
                   "ts": _ts(s, hours_ago), "ledger_status": "DEBITED", "operator": operator,
                   "payee": f"{operator} prepaid"}
    s.recharges[tid] = {"txn_id": tid, "operator": operator, "status": op_status,
                        "operator_ref": f"{operator[:2].upper()}{tid[-6:]}" if op_status == "SUCCESS" else None}
    return tid


def add_refund(s: Store, txn_id, status, days_ago):
    rid = s.next_id("RF")
    created = s.now - timedelta(days=days_ago)
    s.refunds[rid] = {"refund_id": rid, "txn_id": txn_id, "amount": s.txns[txn_id]["amount"],
                      "status": status, "reason": "auto-reversal", "created_at": created.isoformat(timespec="minutes"),
                      "eta": (created + timedelta(days=1)).isoformat(timespec="minutes")}
    if status == "COMPLETED":
        s.refunds[rid]["completed_at"] = (created + timedelta(hours=20)).isoformat(timespec="minutes")
    return rid


def add_merchant_payment(s: Store, merchant_cid, amount, hours_ago, settled: bool, payer="Customer"):
    tid = _txn_id(s)
    s.txns[tid] = {"txn_id": tid, "customer_id": merchant_cid, "type": "MERCHANT_COLLECT",
                   "amount": amount, "ts": _ts(s, hours_ago), "ledger_status": "CREDIT_RECEIVED",
                   "payee": "self", "payer": payer}
    s.settlements[tid] = {"txn_id": tid, "merchant_id": merchant_cid, "amount": amount,
                          "status": "SETTLED" if settled else "PENDING",
                          "expected_at": _ts(s, hours_ago - 24),  # T+1
                          "utr": f"UTR{tid[-10:]}" if settled else None}
    return tid


# ---------------------------------------------------------------- live demo
def seed_demo(s: Store) -> list[dict]:
    """Creates the scripted demo world. Returns the quick-start scenarios for the console."""
    sc = []

    add_customer(s, "C1001", "Ramesh Gowda", "98450 11223", "hi")
    t = add_upi(s, "C1001", 2500, 3, "FAILED", payee="suresh@ybl", bank="Canara Bank")
    sc.append({"label": "Hindi · ₹2,500 UPI deducted, not received", "customer_id": "C1001",
               "message": f"मेरे खाते से ₹2500 कट गए लेकिन दोस्त को पैसे नहीं मिले। ट्रांजैक्शन आईडी {t}"})

    add_customer(s, "C1002", "Priya Nair", "99001 44556", "en")
    add_recharge(s, "C1002", 299, 5, "FAILED", "Jio")
    add_upi(s, "C1002", 120, 30, "SUCCESS", payee="chaipoint@paytm", p2m=True)
    sc.append({"label": "English · Recharge failed (no txn ID)", "customer_id": "C1002",
               "message": "My Jio recharge of Rs 299 failed but the money was deducted. Please help."})

    add_customer(s, "C1003", "Arjun Hegde", "97400 77881", "kn")
    t = add_upi(s, "C1003", 18000, 6, "FAILED", payee="landlord@okicici", bank="ICICI Bank")
    sc.append({"label": "Kannada · ₹18,000 failed → high value, escalates", "customer_id": "C1003",
               "message": f"ನನ್ನ ಖಾತೆಯಿಂದ ₹18000 ಕಡಿತವಾಗಿದೆ ಆದರೆ ಹಣ ತಲುಪಿಲ್ಲ. ವಹಿವಾಟು ಐಡಿ {t}"})

    add_customer(s, "C1004", "Sneha Reddy", "90080 33445", "en")
    t = add_upi(s, "C1004", 1450, 24 * 9, "FAILED", payee="bigbasket@hdfc", p2m=True)
    add_refund(s, t, "STUCK", 8)
    sc.append({"label": "English · Refund stuck 8 days → re-trigger", "customer_id": "C1004",
               "message": f"My refund of ₹1450 for order payment is pending for more than a week. Txn {t}"})

    add_customer(s, "C2001", "Kumar Stores (merchant)", "80500 66778", "en", kind="merchant")
    t = add_merchant_payment(s, "C2001", 3200, 40, settled=False, payer="Anita S")
    sc.append({"label": "Merchant · Settlement not received", "customer_id": "C2001",
               "message": f"Customer paid ₹3200 by QR yesterday but settlement not received in my bank. Txn {t}"})

    add_customer(s, "C1005", "Vikram Shetty", "96320 99001", "en", risk=0.86, prior=4, true_fraud=True)
    t = add_upi(s, "C1005", 4000, 2, "FAILED", payee="quickcash@ybl", bank="Axis Bank")
    sc.append({"label": "English · Risky account (4 disputes/30d) → escalates", "customer_id": "C1005",
               "message": f"Money deducted ₹4000 not received!! refund immediately. {t}"})

    add_customer(s, "C1006", "Meera Iyer", "98860 12121", "en")
    t = add_upi(s, "C1006", 1200, 4, "SUCCESS", payee="tailor.mohan@okhdfc", bank="HDFC Bank")
    sc.append({"label": "English · Actually credited → share proof", "customer_id": "C1006",
               "message": f"I sent ₹1200 to my tailor, he says he didn't get it. Txn ID {t}"})

    add_customer(s, "C1007", "Rahul Verma", "99860 45454", "hi")
    t = add_upi(s, "C1007", 750, 8, "DEEMED", payee="milkman@paytm", bank="SBI", resolves_to="SUCCESS")
    sc.append({"label": "Hinglish · UPI pending at bank → bank ticket", "customer_id": "C1007",
               "message": f"Bhai 750 rupaye kat gaye par milkman ko nahi mile, txn {t}"})
    add_customer(s, "C1008", "Lakshmi Subramanian", "94440 56789", "ta")
    t = add_upi(s, "C1008", 1800, 5, "FAILED", payee="kavitha@oksbi", bank="SBI")
    sc.append({"label": "Tamil · ₹1,800 deducted (Sarvam translates)", "customer_id": "C1008",
               "message": f"என் கணக்கிலிருந்து ₹1800 கழிக்கப்பட்டது ஆனால் பணம் சென்று சேரவில்லை. பரிவர்த்தனை ஐடி {t}"})
    return sc


# ---------------------------------------------------------------- simulation
SCENARIOS = [
    # name, weight
    ("upi_failed_small", 18), ("upi_failed_high", 7), ("upi_deemed", 9), ("upi_success", 9),
    ("recharge_failed", 10), ("recharge_success", 5), ("refund_stuck", 8), ("refund_in_sla", 6),
    ("already_refunded", 5), ("settlement_delayed", 7), ("settled", 4),
    ("risky_customer", 7), ("ambiguous", 5),
]

FIRST = ["Aarav", "Diya", "Kiran", "Lakshmi", "Mohit", "Nisha", "Pooja", "Rohan", "Sanjay", "Tara",
         "Usha", "Varun", "Yash", "Zoya", "Imran", "Gita", "Harish", "Bhavna", "Chetan", "Farah"]
LAST = ["Rao", "Sharma", "Patel", "Gowda", "Khan", "Menon", "Joshi", "Das", "Pillai", "Singh"]


def generate_disputes(s: Store, n: int = 100, seed: int = 7) -> list[dict]:
    """Creates n synthetic disputes in store s. Each item carries hidden ground truth."""
    rng = random.Random(seed)
    names, weights = zip(*SCENARIOS)
    out = []
    for i in range(n):
        kind = rng.choices(names, weights)[0]
        cid = f"S{i:04d}"
        lang = rng.choices(["en", "hi", "kn"], [5, 4, 2])[0]
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        risk, prior, fraud = round(rng.uniform(0.02, 0.4), 2), rng.choice([0, 0, 0, 1]), False
        # ~3% of ordinary-looking customers are actually abusing refunds (hidden; tests recall)
        if kind == "upi_failed_small" and rng.random() < 0.06:
            fraud = True
        if kind == "risky_customer":
            risk, prior = round(rng.uniform(0.72, 0.95), 2), rng.choice([2, 3, 4, 5])
            fraud = rng.random() < 0.7   # most, not all, risky-looking accounts are real abuse
        is_merchant = kind in ("settlement_delayed", "settled")
        add_customer(s, cid, name + (" Traders" if is_merchant else ""), f"9{rng.randint(100000000, 999999999)}",
                     lang, risk, prior, "merchant" if is_merchant else "consumer", fraud)

        issue, show_id = "deducted", rng.random() < 0.7
        expect = {"outcome": "AUTO", "root_cause": None}
        amt = rng.choice([99, 149, 250, 499, 750, 1000, 1200, 1999, 2500, 3000, 4500])

        if kind in ("upi_failed_small", "risky_customer"):
            t = add_upi(s, cid, amt, rng.uniform(1, 30), "FAILED", bank=rng.choice(BANKS))
            expect["root_cause"] = "UPI_FAILED_NO_REVERSAL"
            if fraud:
                expect["outcome"] = "HUMAN"
        elif kind == "upi_failed_high":
            amt = rng.choice([7500, 12000, 18000, 25000, 49000])
            t = add_upi(s, cid, amt, rng.uniform(1, 30), "FAILED", bank=rng.choice(BANKS))
            expect.update(outcome="HUMAN", root_cause="UPI_FAILED_NO_REVERSAL")
        elif kind == "upi_deemed":
            t = add_upi(s, cid, amt, rng.uniform(2, 20), "DEEMED", bank=rng.choice(BANKS),
                        resolves_to=rng.choice(["SUCCESS", "FAILED"]))
            expect["root_cause"] = "UPI_PENDING_AT_BANK"
        elif kind == "upi_success":
            t = add_upi(s, cid, amt, rng.uniform(1, 30), "SUCCESS", bank=rng.choice(BANKS))
            expect["root_cause"] = "UPI_SUCCESS_CREDITED"
        elif kind in ("recharge_failed", "recharge_success"):
            amt = rng.choice([149, 199, 239, 299, 479, 666, 719])
            ok = kind == "recharge_success"
            t = add_recharge(s, cid, amt, rng.uniform(1, 20), "SUCCESS" if ok else "FAILED", rng.choice(OPERATORS))
            issue = "recharge"
            expect["root_cause"] = "RECHARGE_SUCCESS" if ok else "RECHARGE_FAILED"
        elif kind in ("refund_stuck", "refund_in_sla"):
            stuck = kind == "refund_stuck"
            days = rng.randint(6, 12) if stuck else rng.randint(1, 3)
            t = add_upi(s, cid, amt, 24 * days + 2, "FAILED", p2m=True)
            add_refund(s, t, "STUCK" if stuck else "PROCESSING", days)
            issue = "refund"
            expect["root_cause"] = "REFUND_STUCK" if stuck else "REFUND_IN_PROGRESS"
        elif kind == "already_refunded":
            t = add_upi(s, cid, amt, 24 * 3, "FAILED")
            add_refund(s, t, "COMPLETED", 2)
            expect["root_cause"] = "ALREADY_REFUNDED"
        elif kind in ("settlement_delayed", "settled"):
            ok = kind == "settled"
            t = add_merchant_payment(s, cid, amt * 2, rng.uniform(30, 60), settled=ok)
            issue = "merchant"
            expect["root_cause"] = "SETTLED" if ok else "SETTLEMENT_DELAYED"
        elif kind == "ambiguous":
            # Two debits of the same amount, customer gives no txn ID.
            t = add_upi(s, cid, amt, rng.uniform(1, 10), "FAILED")
            add_upi(s, cid, amt, rng.uniform(11, 30), "SUCCESS")
            show_id = False
            expect.update(outcome="NEEDS_INFO", root_cause="AMBIGUOUS_TXN")

        msg = complaint_text(issue, lang, amt if issue != "merchant" else amt * 2, t if show_id else None, rng)
        out.append({"customer_id": cid, "message": msg, "scenario": kind, "expected": expect, "txn_id": t})
    return out
