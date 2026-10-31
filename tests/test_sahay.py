"""Offline tests: no API keys or network needed. Run: python -m unittest -v"""
import asyncio
import os
import unittest

os.environ["SAHAY_NO_LLM"] = "1"      # force the rules path even if a .env has keys
os.environ["SAHAY_NO_SARVAM"] = "1"
os.environ["SAHAY_STEP_DELAY"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from sahay import policy  # noqa: E402
from sahay.agents import Sahay, _fuzzy_txn  # noqa: E402
from sahay.app import app  # noqa: E402
from sahay.lang import detect_language  # noqa: E402
from sahay.metrics import run_simulation  # noqa: E402
from sahay.seed import seed_demo  # noqa: E402
from sahay.store import Store  # noqa: E402
from sahay.voice_agent import chunks, is_goodbye, is_yes, spoken  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class DemoScenarios(unittest.TestCase):
    """Every scripted demo case reaches the expected root cause and outcome."""

    EXPECTED = {
        "C1001": ("UPI_FAILED_NO_REVERSAL", "AWAITING_FOLLOWUP"),
        "C1002": ("RECHARGE_FAILED", "AWAITING_FOLLOWUP"),
        "C1003": ("UPI_FAILED_NO_REVERSAL", "ESCALATED"),    # ₹18,000 > auto-refund limit
        "C1004": ("REFUND_STUCK", "AWAITING_FOLLOWUP"),
        "C2001": ("SETTLEMENT_DELAYED", "AWAITING_FOLLOWUP"),
        "C1005": ("UPI_FAILED_NO_REVERSAL", "ESCALATED"),    # risky account
        "C1006": ("UPI_SUCCESS_CREDITED", "RESOLVED"),
        "C1007": ("UPI_PENDING_AT_BANK", "AWAITING_FOLLOWUP"),
        "C1008": ("UPI_FAILED_NO_REVERSAL", "AWAITING_FOLLOWUP"),
    }

    def test_scenarios_and_followups(self):
        s = Store()
        scenarios = seed_demo(s)
        agent = Sahay(s, use_llm=False, use_sarvam=False)
        cases = {sc["customer_id"]: run(agent.open_case(sc["customer_id"], sc["message"])) for sc in scenarios}
        for cid, (cause, status) in self.EXPECTED.items():
            with self.subTest(customer=cid):
                self.assertEqual(cases[cid]["root_cause"], cause)
                self.assertEqual(cases[cid]["status"], status)
        self.assertIn("नमस्ते", cases["C1001"]["messages"][-1]["text"])   # replies in Hindi

        # Human approves the high-value case; then the clock moves and follow-ups close cases.
        run(agent.human_decision(cases["C1003"]["case_id"], True, "officer"))
        s.advance(24)
        run(agent.run_followups())
        for cid in ("C1001", "C1002", "C1003", "C1004", "C2001", "C1007"):
            with self.subTest(followup=cid):
                self.assertEqual(cases[cid]["status"], "RESOLVED")
        self.assertTrue(any(a["action"] == "REFUND_INITIATED" for a in s.audit))

    def test_asks_for_txn_instead_of_guessing(self):
        s = Store()
        seed_demo(s)
        agent = Sahay(s, use_llm=False, use_sarvam=False)
        case = run(agent.open_case("C1002", "Rs 500 cut from my account, not received"))
        self.assertEqual(case["status"], "NEEDS_INFO")
        run(agent.customer_reply(case["case_id"], "sorry, txn T2610000004"))
        self.assertEqual(case["root_cause"], "UPI_SUCCESS_CREDITED")


class Policy(unittest.TestCase):
    def test_limits(self):
        cust = {"customer_id": "C", "risk_score": 0.1, "kind": "consumer"}
        txn = {"customer_id": "C", "amount": 18000}
        ctx = {"customer": cust, "txn": txn, "refunds": [], "disputes_30d": 0, "confidence": 0.95}
        self.assertEqual(policy.check_action({"type": "REFUND", "amount": 18000}, ctx)["decision"], "HUMAN")
        txn["amount"] = 2000
        self.assertEqual(policy.check_action({"type": "REFUND", "amount": 2000}, ctx)["decision"], "ALLOW")
        cust["risk_score"] = 0.9
        self.assertEqual(policy.check_action({"type": "REFUND", "amount": 2000}, ctx)["decision"], "HUMAN")


class Language(unittest.TestCase):
    def test_detect(self):
        self.assertEqual(detect_language("मेरे पैसे कट गए"), "hi")
        self.assertEqual(detect_language("ನನ್ನ ಹಣ ಕಡಿತವಾಗಿದೆ"), "kn")
        self.assertEqual(detect_language("என் பணம்"), "ta")
        self.assertEqual(detect_language("paise kat gaye lekin nahi mile"), "hi")   # Hinglish
        self.assertEqual(detect_language("my refund is pending"), "en")


class VoiceAgent(unittest.TestCase):
    def test_spoken_drops_ids(self):
        out = spoken("Hi Priya, your ₹299 recharge (T2610000003) failed. We started a refund (Ref RF00021). "
                     "We raised ticket BT00023 with the bank.")
        self.assertNotIn("T2610000003", out)
        self.assertNotIn("RF00021", out)
        self.assertNotIn("BT00023", out)

    def test_intents(self):
        self.assertTrue(is_goodbye("No, thank you."))
        self.assertTrue(is_goodbye("नहीं, धन्यवाद"))
        self.assertFalse(is_goodbye("paise kat gaye lekin nahi mile 2500"))
        self.assertTrue(is_yes("haan ji"))

    def test_chunks(self):
        parts = chunks("One. " * 60)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= 120 for p in parts))

    def test_fuzzy_spoken_txn(self):
        txns = [{"txn_id": "T2610000016"}, {"txn_id": "T2610000020"}]
        self.assertEqual(_fuzzy_txn("T26100000016", txns)["txn_id"], "T2610000016")   # one extra digit
        self.assertIsNone(_fuzzy_txn("T9999999999", txns))


class Simulation(unittest.TestCase):
    def test_metrics(self):
        m = run(run_simulation(100, seed=7))
        self.assertEqual(m["cases"], 100)
        self.assertGreaterEqual(m["auto_resolution_rate"], 70)
        self.assertGreaterEqual(m["escalation_precision"], 80)


class Api(unittest.TestCase):
    def test_endpoints(self):
        with TestClient(app) as c:
            c.post("/api/reset")
            st = c.get("/api/state").json()
            self.assertGreaterEqual(len(st["scenarios"]), 8)
            self.assertFalse(st["integrations"]["llm"]["enabled"])
            self.assertEqual(c.get("/").status_code, 200)
            self.assertEqual(c.get("/mock/customers/C1001").json()["name"], "Ramesh Gowda")
            self.assertEqual(c.post("/api/call/start", json={"customer_id": "C1001"}).status_code, 400)  # no Sarvam


if __name__ == "__main__":
    unittest.main()
