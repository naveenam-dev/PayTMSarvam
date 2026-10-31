"""LLM client. Default provider: Google Gemini (native REST API).

Set GEMINI_API_KEY to enable Gemini. LLM_API_KEY / NVIDIA_API_KEY still work
for an OpenAI-compatible endpoint (NVIDIA NIM). With no key, agents use the
rule-based path, so the demo never depends on network access.

The LLM understands and writes (intake extraction, officer summaries). It never
decides money movement: that stays with the deterministic policy checks.
"""
from __future__ import annotations

import json
import os
import re

import httpx

GEMINI_BASE = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
GEMINI_THINKING = os.getenv("GEMINI_THINKING", "low")  # "" to use the model default
NIM_BASE = os.getenv("LLM_BASE_URL", "https://integrate.api.nvidia.com/v1")
NIM_MODEL = os.getenv("LLM_MODEL", "meta/llama-3.1-70b-instruct")

last_error: str | None = None


def _gemini_key() -> str | None:
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")


def _nim_key() -> str | None:
    return os.getenv("LLM_API_KEY") or os.getenv("NVIDIA_API_KEY")


def provider() -> str | None:
    if os.getenv("SAHAY_NO_LLM") == "1":
        return None
    forced = os.getenv("LLM_PROVIDER")
    if forced in ("gemini", "nim"):
        return forced if (_gemini_key() if forced == "gemini" else _nim_key()) else None
    if _gemini_key():
        return "gemini"
    if _nim_key():
        return "nim"
    return None


def available() -> bool:
    return provider() is not None


def mode() -> str:
    p = provider()
    if p == "gemini":
        return f"Gemini ({GEMINI_MODEL})"
    if p == "nim":
        return f"NIM ({NIM_MODEL})"
    return "Rules engine (offline)"


async def chat(system: str, user: str, max_tokens: int = 1024, json_mode: bool = False) -> str | None:
    global last_error
    p = provider()
    if not p:
        return None
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            if p == "gemini":
                # Thinking tokens count against maxOutputTokens, so keep thinking low and the cap generous;
                # on default thinking a 400-token cap left a 13-token, cut-off answer.
                cfg = {"temperature": 0.1, "maxOutputTokens": max(max_tokens, 2048)}
                if GEMINI_THINKING:
                    cfg["thinkingConfig"] = {"thinkingLevel": GEMINI_THINKING}
                if json_mode:
                    cfg["responseMimeType"] = "application/json"
                body = {"system_instruction": {"parts": [{"text": system}]},
                        "contents": [{"role": "user", "parts": [{"text": user}]}],
                        "generationConfig": cfg}
                url = f"{GEMINI_BASE}/models/{GEMINI_MODEL}:generateContent"
                r = await client.post(url, headers={"x-goog-api-key": _gemini_key()}, json=body)
                if r.status_code == 400 and "thinkingConfig" in cfg:  # model without thinking levels
                    cfg.pop("thinkingConfig")
                    r = await client.post(url, headers={"x-goog-api-key": _gemini_key()}, json=body)
                r.raise_for_status()
                parts = r.json()["candidates"][0]["content"]["parts"]
                text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            else:
                r = await client.post(
                    f"{NIM_BASE}/chat/completions",
                    headers={"Authorization": f"Bearer {_nim_key()}"},
                    json={"model": NIM_MODEL, "temperature": 0.1, "max_tokens": max_tokens,
                          "messages": [{"role": "system", "content": system},
                                       {"role": "user", "content": user}]})
                r.raise_for_status()
                text = r.json()["choices"][0]["message"]["content"]
        last_error = None
        return text.strip() or None
    except Exception as e:  # network, quota, schema: fall back to rules
        last_error = f"{type(e).__name__}: {str(e)[:200]}"
        return None


def _json(out: str | None) -> dict | None:
    if not out:
        return None
    m = re.search(r"\{.*\}", out, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None


INTAKE_SYSTEM = """You extract payment-dispute details from a customer message to Paytm support.
The message may be in any Indian language, Hinglish or English.
Return only JSON: {"issue": one of ["deducted","recharge","refund","merchant","other"],
"txn_id": string or null (Paytm format: T followed by digits), "amount": number or null,
"summary_en": one short English sentence describing the problem}.
"deducted" = money debited but receiver did not get it; "refund" = asking about an existing refund;
"merchant" = a merchant has not received settlement."""


async def extract_intake(text: str) -> dict | None:
    return _json(await chat(INTAKE_SYSTEM, text, 512, json_mode=True))


BRIEF_SYSTEM = """You write a 3-sentence summary for a Paytm disputes officer who must approve or reject
an escalated case. Use only the facts given. Sentence 1: what the customer claims and what the
systems show. Sentence 2: why the AI could not act alone (cite the policy IDs). Sentence 3: what
the officer should check before deciding. Plain English, no markdown."""


async def officer_summary(brief: dict) -> str | None:
    return await chat(BRIEF_SYSTEM, json.dumps(brief, ensure_ascii=False), 400)
