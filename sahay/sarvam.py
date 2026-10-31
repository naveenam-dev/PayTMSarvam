"""Sarvam AI integration: Indian-language voice and translation.

  speech-to-text  /speech-to-text   saaras:v4      customer voice notes -> text + language
  translate       /translate        sarvam-translate:v1 (known source) / mayura:v1 (auto source)
  text-to-speech  /text-to-speech   bulbul:v4-flash  replies read aloud in the customer's language

Set SARVAM_API_KEY to enable. Without it, the console hides voice and the
agents use built-in EN/HI/KN templates.
"""
from __future__ import annotations

import os

import httpx

BASE = os.getenv("SARVAM_BASE_URL", "https://api.sarvam.ai")
STT_MODEL = os.getenv("SARVAM_STT_MODEL", "saaras:v4")
TTS_MODEL = os.getenv("SARVAM_TTS_MODEL", "bulbul:v4-flash")
TTS_SPEAKER = os.getenv("SARVAM_TTS_SPEAKER", "")  # empty = model default voice

# Our short codes <-> Sarvam BCP-47 codes
TO_BCP = {"en": "en-IN", "hi": "hi-IN", "kn": "kn-IN", "ta": "ta-IN", "te": "te-IN", "ml": "ml-IN",
          "bn": "bn-IN", "gu": "gu-IN", "mr": "mr-IN", "pa": "pa-IN", "od": "od-IN"}
FROM_BCP = {v: k for k, v in TO_BCP.items()}

last_error: str | None = None


def key() -> str | None:
    return os.getenv("SARVAM_API_KEY")


def available() -> bool:
    return bool(key()) and os.getenv("SAHAY_NO_SARVAM") != "1"


def _headers() -> dict:
    return {"api-subscription-key": key()}


def _fail(e: Exception) -> None:
    global last_error
    detail = ""
    if isinstance(e, httpx.HTTPStatusError):
        detail = " " + e.response.text[:200]
    last_error = f"{type(e).__name__}{detail}"


async def translate(text: str, target: str, source: str | None = None) -> str | None:
    """Translate text. source=None lets Mayura auto-detect the source language."""
    if not available() or target == source:
        return None
    body = {"input": text[:1900], "target_language_code": TO_BCP.get(target, target)}
    if source:
        body.update(source_language_code=TO_BCP.get(source, source), model="sarvam-translate:v1")
    else:
        body.update(source_language_code="auto", model="mayura:v1")
        body["input"] = body["input"][:990]
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(f"{BASE}/translate", headers=_headers(), json=body)
            r.raise_for_status()
            return r.json().get("translated_text")
    except Exception as e:
        _fail(e)
        return None


async def speech_to_text(audio: bytes, filename: str, content_type: str) -> dict | None:
    """Returns {"transcript": str, "language": short code or None}."""
    if not available():
        return None
    # Browsers send "audio/webm;codecs=opus"; Sarvam accepts "audio/webm" but rejects the codecs suffix.
    content_type = (content_type or "audio/webm").split(";")[0].strip() or "audio/webm"
    try:
        async with httpx.AsyncClient(timeout=40) as client:
            r = await client.post(f"{BASE}/speech-to-text", headers=_headers(),
                                  data={"model": STT_MODEL, "mode": "transcribe"},
                                  files={"file": (filename, audio, content_type)})
            r.raise_for_status()
            j = r.json()
            code = j.get("language_code")
            return {"transcript": j.get("transcript", ""), "language": FROM_BCP.get(code, None),
                    "language_code": code}
    except Exception as e:
        _fail(e)
        return None


async def text_to_speech(text: str, lang: str) -> str | None:
    """Returns base64 WAV audio."""
    if not available():
        return None
    code = TO_BCP.get(lang, "en-IN")
    body = {"text": text[:2400], "model": TTS_MODEL}
    if TTS_SPEAKER:
        body["speaker"] = TTS_SPEAKER
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Current docs use "language_code"; older API versions use "target_language_code".
            for field in ("language_code", "target_language_code"):
                r = await client.post(f"{BASE}/text-to-speech", headers=_headers(), json={**body, field: code})
                if r.status_code not in (400, 422):
                    break
            r.raise_for_status()
            return "".join(r.json().get("audios", [])) or None
    except Exception as e:
        _fail(e)
        return None
