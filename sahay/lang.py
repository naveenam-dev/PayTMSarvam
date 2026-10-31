"""Language detection and message templates (English, Hindi, Kannada).

Rule-based so the demo works offline. When an LLM is configured, the Intake
agent uses it for extraction and these templates remain the safe fallback.
"""
from __future__ import annotations

import re

HINGLISH = {"paise", "paisa", "rupaye", "kat", "gaye", "nahi", "mile", "bhai", "mera", "mere", "kab", "aaya", "hua"}


SCRIPTS = [("kn", r"[ಀ-೿]"), ("te", r"[ఀ-౿]"), ("ta", r"[஀-௿]"),
           ("ml", r"[ഀ-ൿ]"), ("bn", r"[ঀ-৿]"), ("gu", r"[઀-૿]"),
           ("pa", r"[਀-੿]"), ("od", r"[଀-୿]"), ("hi", r"[ऀ-ॿ]")]


def detect_language(text: str) -> str:
    for code, pattern in SCRIPTS:
        if re.search(pattern, text):
            return code
    words = set(re.findall(r"[a-z]+", text.lower()))
    if len(words & HINGLISH) >= 2:
        return "hi"
    return "en"


LANG_NAMES = {"en": "English", "hi": "Hindi", "kn": "Kannada", "ta": "Tamil", "te": "Telugu", "ml": "Malayalam",
              "bn": "Bengali", "gu": "Gujarati", "mr": "Marathi", "pa": "Punjabi", "od": "Odia"}

# ------------------------------------------------------------ complaints (simulation)
COMPLAINTS = {
    "deducted": {
        "en": ["Money deducted from my account but not received. Amount ₹{amt}. {ref}",
               "I paid Rs {amt} via UPI, amount debited but receiver did not get it {ref}"],
        "hi": ["मेरे खाते से ₹{amt} कट गए लेकिन पैसे नहीं पहुँचे। {ref}",
               "Paise kat gaye lekin nahi mile, {amt} rupaye {ref}"],
        "kn": ["ನನ್ನ ಖಾತೆಯಿಂದ ₹{amt} ಕಡಿತವಾಗಿದೆ ಆದರೆ ಹಣ ತಲುಪಿಲ್ಲ. {ref}"],
    },
    "recharge": {
        "en": ["My recharge of Rs {amt} failed but money got deducted {ref}"],
        "hi": ["मेरा ₹{amt} का रिचार्ज फेल हो गया पर पैसे कट गए {ref}"],
        "kn": ["ನನ್ನ ₹{amt} ರೀಚಾರ್ಜ್ ವಿಫಲವಾಗಿದೆ ಆದರೆ ಹಣ ಕಡಿತವಾಗಿದೆ {ref}"],
    },
    "refund": {
        "en": ["My refund of ₹{amt} is still pending {ref}"],
        "hi": ["₹{amt} का रिफंड अभी तक नहीं आया {ref}"],
        "kn": ["₹{amt} ರಿಫಂಡ್ ಇನ್ನೂ ಬಂದಿಲ್ಲ {ref}"],
    },
    "merchant": {
        "en": ["Customer paid ₹{amt} but settlement not received in my bank {ref}"],
        "hi": ["ग्राहक का ₹{amt} का पेमेंट मेरे खाते में नहीं आया, सेटलमेंट पेंडिंग है {ref}"],
        "kn": ["ಗ್ರಾಹಕರ ₹{amt} ಪಾವತಿ ಸೆಟಲ್ಮೆಂಟ್ ನನ್ನ ಖಾತೆಗೆ ಬಂದಿಲ್ಲ {ref}"],
    },
}
REF = {"en": "Txn ID {id}", "hi": "ट्रांजैक्शन आईडी {id}", "kn": "ವಹಿವಾಟು ಐಡಿ {id}"}


def complaint_text(issue, lang, amt, txn_id, rng) -> str:
    tpl = rng.choice(COMPLAINTS[issue][lang])
    ref = REF[lang].format(id=txn_id) if txn_id else ""
    return tpl.format(amt=amt, ref=ref).strip()


# ------------------------------------------------------------ replies to customer
REPLIES = {
    "REFUND_INITIATED": {
        "en": "Hi {name}, we checked your ₹{amt} payment ({txn}). It failed at {bank} and the money was not reversed. We have started a refund (Ref {ref}). It will reach your account by {eta}.",
        "hi": "नमस्ते {name}, हमने आपका ₹{amt} का भुगतान ({txn}) जाँचा। यह {bank} पर फेल हुआ और पैसे वापस नहीं आए थे। हमने रिफंड शुरू कर दिया है (Ref {ref})। पैसे {eta} तक आपके खाते में आ जाएँगे।",
        "kn": "ನಮಸ್ಕಾರ {name}, ನಿಮ್ಮ ₹{amt} ಪಾವತಿ ({txn}) {bank} ನಲ್ಲಿ ವಿಫಲವಾಗಿದೆ. ನಾವು ರಿಫಂಡ್ ಪ್ರಾರಂಭಿಸಿದ್ದೇವೆ (Ref {ref}). ಹಣ {eta} ಒಳಗೆ ನಿಮ್ಮ ಖಾತೆಗೆ ಬರುತ್ತದೆ.",
    },
    "RECHARGE_REFUND": {
        "en": "Hi {name}, your ₹{amt} {operator} recharge ({txn}) failed at the operator. We have started a refund (Ref {ref}). It will reach you by {eta}.",
        "hi": "नमस्ते {name}, आपका ₹{amt} का {operator} रिचार्ज ({txn}) ऑपरेटर की ओर से फेल हुआ। हमने रिफंड शुरू कर दिया है (Ref {ref})। पैसे {eta} तक आ जाएँगे।",
        "kn": "ನಮಸ್ಕಾರ {name}, ನಿಮ್ಮ ₹{amt} {operator} ರೀಚಾರ್ಜ್ ({txn}) ವಿಫಲವಾಗಿದೆ. ರಿಫಂಡ್ ಪ್ರಾರಂಭಿಸಲಾಗಿದೆ (Ref {ref}). ಹಣ {eta} ಒಳಗೆ ಬರುತ್ತದೆ.",
    },
    "BANK_TICKET": {
        "en": "Hi {name}, your ₹{amt} payment ({txn}) is pending confirmation at {bank}. We raised ticket {ref} with the bank. You will get an update by {eta}. You do not need to pay again.",
        "hi": "नमस्ते {name}, आपका ₹{amt} का भुगतान ({txn}) {bank} पर पुष्टि के लिए लंबित है। हमने बैंक के साथ टिकट {ref} दर्ज किया है। {eta} तक अपडेट मिलेगा। दोबारा भुगतान न करें।",
        "kn": "ನಮಸ್ಕಾರ {name}, ನಿಮ್ಮ ₹{amt} ಪಾವತಿ ({txn}) {bank} ನಲ್ಲಿ ಬಾಕಿ ಇದೆ. ಬ್ಯಾಂಕ್‌ನಲ್ಲಿ ಟಿಕೆಟ್ {ref} ದಾಖಲಿಸಿದ್ದೇವೆ. {eta} ಒಳಗೆ ಮಾಹಿತಿ ಸಿಗುತ್ತದೆ.",
    },
    "CREDITED_PROOF": {
        "en": "Hi {name}, your ₹{amt} payment ({txn}) was successful. {bank} credited it to {payee}. Please share this reference with them: UPI RRN {ref}.",
        "hi": "नमस्ते {name}, आपका ₹{amt} का भुगतान ({txn}) सफल रहा। {bank} ने यह {payee} के खाते में जमा किया है। कृपया उन्हें यह रेफरेंस दें: UPI RRN {ref}।",
        "kn": "ನಮಸ್ಕಾರ {name}, ನಿಮ್ಮ ₹{amt} ಪಾವತಿ ({txn}) ಯಶಸ್ವಿಯಾಗಿದೆ. {payee} ಅವರ ಖಾತೆಗೆ ಜಮಾ ಆಗಿದೆ. UPI RRN {ref} ಅನ್ನು ಅವರಿಗೆ ತಿಳಿಸಿ.",
    },
    "RECHARGE_DONE": {
        "en": "Hi {name}, your ₹{amt} {operator} recharge ({txn}) was successful. Operator reference: {ref}. It can take up to 30 minutes to show on your phone.",
        "hi": "नमस्ते {name}, आपका ₹{amt} का {operator} रिचार्ज ({txn}) सफल रहा। ऑपरेटर रेफरेंस: {ref}। फ़ोन पर दिखने में 30 मिनट तक लग सकते हैं।",
        "kn": "ನಮಸ್ಕಾರ {name}, ನಿಮ್ಮ ₹{amt} {operator} ರೀಚಾರ್ಜ್ ({txn}) ಯಶಸ್ವಿಯಾಗಿದೆ. ಆಪರೇಟರ್ ರೆಫರೆನ್ಸ್: {ref}.",
    },
    "REFUND_RETRIGGERED": {
        "en": "Hi {name}, your ₹{amt} refund ({txn}) was stuck at the bank. We sent it again (Ref {ref}). It will reach you by {eta}. Sorry for the delay.",
        "hi": "नमस्ते {name}, आपका ₹{amt} का रिफंड ({txn}) बैंक पर अटका था। हमने इसे दोबारा भेजा है (Ref {ref})। पैसे {eta} तक आ जाएँगे। देरी के लिए खेद है।",
        "kn": "ನಮಸ್ಕಾರ {name}, ನಿಮ್ಮ ₹{amt} ರಿಫಂಡ್ ({txn}) ಬ್ಯಾಂಕ್‌ನಲ್ಲಿ ಸಿಲುಕಿತ್ತು. ಮತ್ತೆ ಕಳುಹಿಸಿದ್ದೇವೆ (Ref {ref}). ಹಣ {eta} ಒಳಗೆ ಬರುತ್ತದೆ.",
    },
    "REFUND_IN_PROGRESS": {
        "en": "Hi {name}, your ₹{amt} refund ({txn}, Ref {ref}) is in progress and within the bank timeline. It will reach you by {eta}.",
        "hi": "नमस्ते {name}, आपका ₹{amt} का रिफंड ({txn}, Ref {ref}) प्रोसेस में है। पैसे {eta} तक आ जाएँगे।",
        "kn": "ನಮಸ್ಕಾರ {name}, ನಿಮ್ಮ ₹{amt} ರಿಫಂಡ್ ({txn}, Ref {ref}) ಪ್ರಕ್ರಿಯೆಯಲ್ಲಿದೆ. ಹಣ {eta} ಒಳಗೆ ಬರುತ್ತದೆ.",
    },
    "ALREADY_REFUNDED": {
        "en": "Hi {name}, the ₹{amt} for {txn} was already refunded on {eta} (Ref {ref}). Please check your bank statement for this reference.",
        "hi": "नमस्ते {name}, {txn} के ₹{amt} {eta} को पहले ही रिफंड हो चुके हैं (Ref {ref})। कृपया बैंक स्टेटमेंट में यह रेफरेंस देखें।",
        "kn": "ನಮಸ್ಕಾರ {name}, {txn} ನ ₹{amt} ಈಗಾಗಲೇ {eta} ರಂದು ರಿಫಂಡ್ ಆಗಿದೆ (Ref {ref}).",
    },
    "SETTLEMENT_REPUSHED": {
        "en": "Hi {name}, the settlement for ₹{amt} ({txn}) was delayed. We have pushed it again to your bank. It will reach you by {eta}.",
        "hi": "नमस्ते {name}, ₹{amt} ({txn}) का सेटलमेंट देरी से था। हमने इसे दोबारा आपके बैंक में भेजा है। पैसे {eta} तक आ जाएँगे।",
        "kn": "ನಮಸ್ಕಾರ {name}, ₹{amt} ({txn}) ಸೆಟಲ್ಮೆಂಟ್ ತಡವಾಗಿತ್ತು. ಮತ್ತೆ ನಿಮ್ಮ ಬ್ಯಾಂಕ್‌ಗೆ ಕಳುಹಿಸಿದ್ದೇವೆ. {eta} ಒಳಗೆ ಬರುತ್ತದೆ.",
    },
    "SETTLED": {
        "en": "Hi {name}, the ₹{amt} for {txn} was settled to your bank. UTR: {ref}.",
        "hi": "नमस्ते {name}, {txn} के ₹{amt} आपके बैंक में सेटल हो चुके हैं। UTR: {ref}।",
        "kn": "ನಮಸ್ಕಾರ {name}, {txn} ನ ₹{amt} ನಿಮ್ಮ ಬ್ಯಾಂಕ್‌ಗೆ ಸೆಟಲ್ ಆಗಿದೆ. UTR: {ref}.",
    },
    "SETTLEMENT_IN_WINDOW": {
        "en": "Hi {name}, the ₹{amt} payment ({txn}) is in today's T+1 settlement batch. It will reach your bank by {eta}.",
        "hi": "नमस्ते {name}, ₹{amt} ({txn}) का पेमेंट T+1 सेटलमेंट बैच में है। पैसे {eta} तक आपके बैंक में आ जाएँगे।",
    },
    "ESCALATED": {
        "en": "Hi {name}, thank you for your patience. A specialist is reviewing your ₹{amt} case ({txn}). You will get an update within 4 hours.",
        "hi": "नमस्ते {name}, धैर्य रखने के लिए धन्यवाद। एक विशेषज्ञ आपका ₹{amt} का केस ({txn}) देख रहे हैं। 4 घंटे में अपडेट मिलेगा।",
        "kn": "ನಮಸ್ಕಾರ {name}, ನಿಮ್ಮ ₹{amt} ಪ್ರಕರಣವನ್ನು ({txn}) ತಜ್ಞರು ಪರಿಶೀಲಿಸುತ್ತಿದ್ದಾರೆ. 4 ಗಂಟೆಗಳಲ್ಲಿ ಮಾಹಿತಿ ಸಿಗುತ್ತದೆ.",
    },
    "NEEDS_INFO": {
        "en": "Hi {name}, I found more than one matching payment. Please share the transaction ID from your Paytm passbook so I can check the right one.",
        "hi": "नमस्ते {name}, मुझे एक से ज़्यादा मिलते-जुलते भुगतान मिले। कृपया Paytm पासबुक से ट्रांजैक्शन आईडी भेजें।",
        "kn": "ನಮಸ್ಕಾರ {name}, ಒಂದಕ್ಕಿಂತ ಹೆಚ್ಚು ಪಾವತಿಗಳು ಸಿಕ್ಕಿವೆ. ದಯವಿಟ್ಟು Paytm ಪಾಸ್‌ಬುಕ್‌ನಿಂದ ವಹಿವಾಟು ಐಡಿ ಕಳುಹಿಸಿ.",
    },
    "NOT_FOUND": {
        "en": "Hi {name}, I could not find this payment on your account. Please share the transaction ID from your Paytm passbook.",
        "hi": "नमस्ते {name}, यह भुगतान आपके खाते में नहीं मिला। कृपया Paytm पासबुक से ट्रांजैक्शन आईडी भेजें।",
        "kn": "ನಮಸ್ಕಾರ {name}, ಈ ಪಾವತಿ ನಿಮ್ಮ ಖಾತೆಯಲ್ಲಿ ಸಿಗಲಿಲ್ಲ. ದಯವಿಟ್ಟು ವಹಿವಾಟು ಐಡಿ ಕಳುಹಿಸಿ.",
    },
    "RESOLVED": {
        "en": "Hi {name}, good news: ₹{amt} for {txn} has reached your account. Your case {case} is now closed.",
        "hi": "नमस्ते {name}, अच्छी खबर: {txn} के ₹{amt} आपके खाते में आ गए हैं। आपका केस {case} बंद कर दिया गया है।",
        "kn": "ನಮಸ್ಕಾರ {name}, {txn} ನ ₹{amt} ನಿಮ್ಮ ಖಾತೆಗೆ ಬಂದಿದೆ. ನಿಮ್ಮ ಪ್ರಕರಣ {case} ಮುಕ್ತಾಯವಾಗಿದೆ.",
    },
    "BANK_CONFIRMED_SUCCESS": {
        "en": "Hi {name}, {bank} confirmed that ₹{amt} ({txn}) was credited to {payee}. UPI RRN {ref}. Your case {case} is now closed.",
        "hi": "नमस्ते {name}, {bank} ने पुष्टि की है कि ₹{amt} ({txn}) {payee} के खाते में जमा हुए। UPI RRN {ref}। आपका केस {case} बंद है।",
        "kn": "ನಮಸ್ಕಾರ {name}, ₹{amt} ({txn}) {payee} ಅವರಿಗೆ ಜಮಾ ಆಗಿದೆ ಎಂದು {bank} ದೃಢಪಡಿಸಿದೆ. UPI RRN {ref}.",
    },
    "REJECTED": {
        "en": "Hi {name}, after review we could not approve a refund for {txn}. Reason: {reason}. Reply here if you want to share more details.",
        "hi": "नमस्ते {name}, जाँच के बाद {txn} के लिए रिफंड मंज़ूर नहीं हो सका। कारण: {reason}। ज़्यादा जानकारी देने के लिए यहाँ जवाब दें।",
        "kn": "ನಮಸ್ಕಾರ {name}, ಪರಿಶೀಲನೆಯ ನಂತರ {txn} ಗೆ ರಿಫಂಡ್ ಅನುಮೋದಿಸಲಾಗಲಿಲ್ಲ. ಕಾರಣ: {reason}.",
    },
}


class _Safe(dict):
    def __missing__(self, key):
        return "-"


def has_template(key: str, lang: str) -> bool:
    return lang in REPLIES[key]


def reply(key: str, lang: str, **kw) -> str:
    tpl = REPLIES[key].get(lang) or REPLIES[key]["en"]
    if "amt" in kw and isinstance(kw["amt"], (int, float)):
        kw["amt"] = f"{kw['amt']:,.0f}"
    if "eta" in kw and kw["eta"] and "T" in str(kw["eta"]):
        kw["eta"] = pretty_ts(kw["eta"])
    return tpl.format_map(_Safe(kw))


def pretty_ts(iso: str) -> str:
    from datetime import datetime
    d = datetime.fromisoformat(iso)
    return d.strftime("%d %b, %I:%M %p").lstrip("0")
