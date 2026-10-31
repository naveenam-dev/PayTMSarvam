"""FastAPI app: mock Paytm APIs, Sahay AI case API, WebSocket feed, human console."""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=value lines). Real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from . import llm, sarvam  # noqa: E402
from . import voice_agent as va  # noqa: E402
from .agents import Sahay  # noqa: E402
from .metrics import run_simulation, summarise  # noqa: E402
from .policy import POLICY_KB  # noqa: E402
from .seed import seed_demo  # noqa: E402
from .store import Store  # noqa: E402
from .systems import PaytmSystems  # noqa: E402

STATIC = Path(__file__).resolve().parent.parent / "static"
STEP_DELAY = float(os.getenv("SAHAY_STEP_DELAY", "0.5"))  # seconds between steps, so judges can follow

app = FastAPI(title="Sahay AI — TeamApex")
clients: set[WebSocket] = set()


async def broadcast(event: dict) -> None:
    event = {**event, "clock": W.store.ts(), "metrics": summarise(list(W.store.cases.values())),
             "integrations": integrations()}
    for ws in list(clients):
        try:
            await ws.send_json(event)
        except Exception:
            clients.discard(ws)


class World:
    def __init__(self):
        self.reset()

    def reset(self):
        self.store = Store()
        self.scenarios = seed_demo(self.store)
        self.systems = PaytmSystems(self.store)
        self.agent = Sahay(self.store, emit=broadcast, step_delay=STEP_DELAY)


W = World()
_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


# ------------------------------------------------------------------ console
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/state")
def state():
    s = W.store
    return {"clock": s.ts(), "mode": llm.mode(), "integrations": integrations(), "scenarios": W.scenarios,
            "customers": [{k: v for k, v in c.items() if not k.startswith("_")} for c in s.customers.values()],
            "cases": list(s.cases.values()), "metrics": summarise(list(s.cases.values())),
            "policies": POLICY_KB}


class NewCase(BaseModel):
    customer_id: str
    message: str
    channel: str = "chat"


@app.post("/api/cases")
async def new_case(body: NewCase):
    if body.customer_id not in W.store.customers:
        raise HTTPException(404, "unknown customer")
    _spawn(W.agent.open_case(body.customer_id, body.message, body.channel))
    return {"ok": True}


class Reply(BaseModel):
    text: str


@app.post("/api/cases/{case_id}/reply")
async def customer_reply(case_id: str, body: Reply):
    if case_id not in W.store.cases:
        raise HTTPException(404)
    _spawn(W.agent.customer_reply(case_id, body.text))
    return {"ok": True}


class Decision(BaseModel):
    approve: bool
    officer: str = "Disputes officer"
    note: str = ""


@app.post("/api/cases/{case_id}/decision")
async def decision(case_id: str, body: Decision):
    if case_id not in W.store.cases:
        raise HTTPException(404)
    case = await W.agent.human_decision(case_id, body.approve, body.officer, body.note)
    return {"status": case["status"]}


class Advance(BaseModel):
    hours: float = 24


@app.post("/api/clock/advance")
async def advance(body: Advance):
    W.store.advance(body.hours)
    log = await W.agent.run_followups()
    await broadcast({"type": "clock", "log": log})
    return {"clock": W.store.ts(), "log": log}


class Sim(BaseModel):
    n: int = 100
    seed: int = 7


@app.post("/api/simulate")
async def simulate(body: Sim):
    return await run_simulation(body.n, body.seed)


@app.post("/api/reset")
async def reset():
    W.reset()
    await broadcast({"type": "reset"})
    return {"ok": True}


def integrations() -> dict:
    return {"llm": {"enabled": llm.available(), "mode": llm.mode(), "last_error": llm.last_error},
            "sarvam": {"enabled": sarvam.available(), "stt": sarvam.STT_MODEL, "tts": sarvam.TTS_MODEL,
                       "last_error": sarvam.last_error}}


@app.get("/api/integrations/test")
async def test_integrations():
    """Live check of both providers with tiny requests."""
    out = {}
    if llm.available():
        got = await llm.extract_intake("Mere 500 rupaye kat gaye, txn T2610000001")
        out["llm"] = {"ok": bool(got), "mode": llm.mode(), "result": got, "error": llm.last_error}
    else:
        out["llm"] = {"ok": False, "error": "No GEMINI_API_KEY set"}
    if sarvam.available():
        tr = await sarvam.translate("Your refund has been started.", "hi", "en")
        audio = await sarvam.text_to_speech("नमस्ते", "hi")
        out["sarvam"] = {"ok": bool(tr) and bool(audio), "translate": tr,
                         "tts_bytes_b64": len(audio or ""), "error": sarvam.last_error}
    else:
        out["sarvam"] = {"ok": False, "error": "No SARVAM_API_KEY set"}
    return out


@app.post("/api/voice")
async def voice(customer_id: str = Form(...), audio: UploadFile = File(...)):
    """Customer voice note -> Sarvam speech-to-text -> new case on the voice channel."""
    if customer_id not in W.store.customers:
        raise HTTPException(404, "unknown customer")
    if not sarvam.available():
        raise HTTPException(400, "Sarvam is not configured (set SARVAM_API_KEY)")
    data = await audio.read()
    stt = await sarvam.speech_to_text(data, audio.filename or "voice.webm", audio.content_type or "audio/webm")
    if not stt or not stt["transcript"]:
        raise HTTPException(502, f"Speech-to-text failed: {sarvam.last_error or 'empty transcript'}")
    _spawn(W.agent.open_case(customer_id, stt["transcript"], "voice", stt["language"]))
    return stt


class CallStart(BaseModel):
    customer_id: str


@app.post("/api/call/start")
async def call_start(body: CallStart):
    """Voice agent picks up: greeting + a 'checking' filler, both pre-synthesised."""
    cust = W.store.customers.get(body.customer_id)
    if not cust:
        raise HTTPException(404, "unknown customer")
    if not sarvam.available():
        raise HTTPException(400, "Sarvam is not configured (set SARVAM_API_KEY)")
    lang = cust["language"]
    greet = await va.phrase("greet", lang, name=cust["name"].split(" (")[0].split()[0])
    filler = await va.phrase("filler", lang)
    greet_audio, filler_audio = await asyncio.gather(sarvam.text_to_speech(greet, lang),
                                                     sarvam.text_to_speech(filler, lang))
    if not greet_audio:
        raise HTTPException(502, f"Text-to-speech failed: {sarvam.last_error}")
    return {"lang": lang, "text": greet, "audio_b64": greet_audio, "filler_audio_b64": filler_audio}


@app.post("/api/call/turn")
async def call_turn(customer_id: str = Form(...), lang: str = Form("en"), case_id: str = Form(""),
                    audio: UploadFile = File(...)):
    """One spoken turn: customer audio in, Sahay's spoken reply out."""
    if customer_id not in W.store.customers:
        raise HTTPException(404, "unknown customer")
    t0 = time.perf_counter()
    stt = await sarvam.speech_to_text(await audio.read(), audio.filename or "turn.webm",
                                      audio.content_type or "audio/webm")
    if stt is None:
        raise HTTPException(502, f"Speech-to-text failed: {sarvam.last_error}")
    t1 = time.perf_counter()
    heard = (stt["transcript"] or "").strip()
    case = W.store.cases.get(case_id)
    end_call, say = False, []

    if not heard:
        say.append(await va.phrase("again", lang))
    elif case and case["status"] == "NEEDS_INFO":
        before = len(case["messages"])
        await W.agent.customer_reply(case_id, heard)
        lang = case["language"] or lang
        say += [va.spoken(m["text"]) for m in case["messages"][before:] if m["from"] == "sahay"]
        if case["status"] != "NEEDS_INFO":
            say.append(await va.phrase("more", lang))
    elif case and va.is_goodbye(heard):
        say.append(await va.phrase("bye", lang))
        end_call = True
    elif case and va.is_yes(heard):
        say.append(await va.phrase("ask", lang))
    else:
        case = await W.agent.open_case(customer_id, heard, "call", stt["language"])
        lang = case["language"] or lang
        say += [va.spoken(m["text"]) for m in case["messages"] if m["from"] == "sahay"]
        if case["status"] != "NEEDS_INFO":
            say.append(await va.phrase("more", lang))

    text = " ".join(s for s in say if s)
    t2 = time.perf_counter()
    parts = va.chunks(text)
    audios = await asyncio.gather(*(sarvam.text_to_speech(p, lang) for p in parts))
    t3 = time.perf_counter()
    return {"heard": heard, "heard_language": stt.get("language_code"), "lang": lang, "say": text,
            "audio_b64s": [a for a in audios if a], "case_id": case["case_id"] if case else "",
            "status": case["status"] if case else None, "end_call": end_call,
            "timing_s": {"stt": round(t1 - t0, 2), "agents": round(t2 - t1, 2), "tts": round(t3 - t2, 2)}}


class Speak(BaseModel):
    text: str
    lang: str = "en"


@app.post("/api/tts")
async def tts(body: Speak):
    if not sarvam.available():
        raise HTTPException(400, "Sarvam is not configured (set SARVAM_API_KEY)")
    audio = await sarvam.text_to_speech(body.text, body.lang)
    if not audio:
        raise HTTPException(502, f"Text-to-speech failed: {sarvam.last_error}")
    return {"audio_b64": audio}


@app.get("/api/audit")
def audit():
    return W.store.audit[-200:]


@app.websocket("/ws")
async def ws(socket: WebSocket):
    await socket.accept()
    clients.add(socket)
    try:
        while True:
            await socket.receive_text()
    except WebSocketDisconnect:
        clients.discard(socket)


# ------------------------------------------------------------------ mock Paytm systems
@app.get("/mock/customers/{cid}")
def m_customer(cid: str):
    c = W.systems.get_customer(cid) or {}
    return {k: v for k, v in c.items() if not k.startswith("_")}


@app.get("/mock/ledger/{txn_id}")
def m_ledger(txn_id: str):
    return W.systems.ledger_lookup(txn_id) or {}


@app.get("/mock/upi/{rrn}")
def m_upi(rrn: str):
    n = W.systems.upi_status(rrn) or {}
    return {k: v for k, v in n.items() if k != "resolves_to"}


@app.get("/mock/recharge/{txn_id}")
def m_recharge(txn_id: str):
    return W.systems.recharge_status(txn_id) or {}


@app.get("/mock/settlements/{txn_id}")
def m_settlement(txn_id: str):
    return W.systems.settlement_status(txn_id) or {}


@app.get("/mock/refunds/{txn_id}")
def m_refunds(txn_id: str):
    return W.systems.refunds_for_txn(txn_id)


@app.get("/mock/tickets")
def m_tickets():
    return list(W.store.tickets.values())


@app.get("/mock/messages")
def m_messages():
    return W.store.messages[-100:]
