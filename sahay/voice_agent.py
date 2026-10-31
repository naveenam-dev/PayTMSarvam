"""Voice agent: turn-based spoken conversation on top of the Sahay agents.

Each customer turn: Sarvam speech-to-text -> Sahay agents (same pipeline and
policy checks as chat) -> short spoken reply -> Sarvam text-to-speech.
"""
from __future__ import annotations

import re

from . import sarvam

PHRASES = {
    "greet": {
        "en": "Hello {name}, this is Sahay from Paytm support. Please tell me what went wrong with your payment.",
        "hi": "नमस्ते {name}, मैं Paytm सपोर्ट से सहाय बोल रहा हूँ। बताइए, आपके पेमेंट में क्या दिक्कत हुई?",
        "kn": "ನಮಸ್ಕಾರ {name}, ನಾನು Paytm ಸಹಾಯ. ನಿಮ್ಮ ಪಾವತಿಯಲ್ಲಿ ಏನು ತೊಂದರೆಯಾಗಿದೆ ಎಂದು ಹೇಳಿ.",
    },
    "filler": {
        "en": "One moment, I am checking your transaction.",
        "hi": "एक सेकंड, मैं आपका ट्रांजैक्शन चेक कर रहा हूँ।",
        "kn": "ಒಂದು ಕ್ಷಣ, ನಿಮ್ಮ ವಹಿವಾಟನ್ನು ಪರಿಶೀಲಿಸುತ್ತಿದ್ದೇನೆ.",
    },
    "more": {
        "en": "I have also sent these details by SMS. Is there anything else I can help you with?",
        "hi": "मैंने यह जानकारी SMS पर भी भेज दी है। क्या मैं और कुछ मदद कर सकता हूँ?",
        "kn": "ಈ ವಿವರಗಳನ್ನು SMS ಮೂಲಕವೂ ಕಳುಹಿಸಿದ್ದೇನೆ. ಇನ್ನೇನಾದರೂ ಸಹಾಯ ಬೇಕೇ?",
    },
    "ask": {
        "en": "Sure, please tell me the problem.",
        "hi": "ज़रूर, बताइए क्या दिक्कत है।",
        "kn": "ಖಂಡಿತ, ತೊಂದರೆ ಏನು ಎಂದು ಹೇಳಿ.",
    },
    "again": {
        "en": "Sorry, I could not hear you clearly. Please say that again.",
        "hi": "माफ़ कीजिए, आवाज़ साफ़ नहीं आई। कृपया फिर से बोलिए।",
        "kn": "ಕ್ಷಮಿಸಿ, ಸರಿಯಾಗಿ ಕೇಳಿಸಲಿಲ್ಲ. ದಯವಿಟ್ಟು ಮತ್ತೆ ಹೇಳಿ.",
    },
    "bye": {
        "en": "Thank you for calling Paytm. Have a good day!",
        "hi": "Paytm को कॉल करने के लिए धन्यवाद। आपका दिन शुभ हो!",
        "kn": "Paytm ಗೆ ಕರೆ ಮಾಡಿದ್ದಕ್ಕೆ ಧನ್ಯವಾದಗಳು. ಶುಭ ದಿನ!",
    },
}

# Only checked on short utterances after a case is answered, so "paise nahi mile" is not a goodbye.
GOODBYE_RE = re.compile(r"\b(no|nope|nothing|that'?s all|thanks?|thank you|bye|ok bye|nahi|nahin|bas|shukriya|"
                        r"dhanyavaad|dhanyawad|illa|saaku|saku)\b|नहीं|बस|धन्यवाद|शुक्रिया|ಇಲ್ಲ|ಸಾಕು|ಧನ್ಯವಾದ", re.I)
YES_RE = re.compile(r"\b(yes|yeah|haan|han|ha|ji|howdu|hoon)\b|हाँ|हां|जी|ಹೌದು", re.I)


async def phrase(key: str, lang: str, **kw) -> str:
    table = PHRASES[key]
    if lang in table:
        return table[lang].format(**kw)
    english = table["en"].format(**kw)
    if lang != "en" and sarvam.available():
        return await sarvam.translate(english, lang, "en") or english
    return english


def spoken(text: str) -> str:
    """Chat reply -> something natural to say aloud: drop long IDs and references (they go by SMS)."""
    t = re.sub(r"\s*\([^()]*(?:T\d{6,}|Ref|RRN|UTR)[^()]*\)", "", text)
    t = re.sub(r"\b(?:UPI RRN|UTR:?|Ref|ticket)\s*[A-Z]*\d{4,}\b", "", t, flags=re.I)
    t = re.sub(r"\bT\d{8,}\b", "", t)
    t = re.sub(r"\s*\b[A-Z]{2,4}\d{4,}\b", "", t)  # ticket / refund IDs like BT00023, RF00021
    t = re.sub(r"\s+([.,।])", r"\1", t)
    t = re.sub(r"[^.।!?]*:\s*[.।]", "", t)  # sentence that only introduced a removed reference
    return re.sub(r"\s{2,}", " ", t).strip()


def chunks(text: str, max_len: int = 110) -> list[str]:
    """Split into sentence groups so text-to-speech can run in parallel and play in order."""
    out, cur = [], ""
    for sent in re.split(r"(?<=[.!?।])\s+", text):
        if cur and len(cur) + len(sent) > max_len:
            out.append(cur)
            cur = sent
        else:
            cur = f"{cur} {sent}".strip()
    return out + ([cur] if cur else [])


def is_short(text: str) -> bool:
    return len(text.split()) <= 6 and not re.search(r"\d{2,}", text)


def is_goodbye(text: str) -> bool:
    return is_short(text) and bool(GOODBYE_RE.search(text))


def is_yes(text: str) -> bool:
    return is_short(text) and bool(YES_RE.search(text)) and not GOODBYE_RE.search(text)
