"""Safety policy: explicit allow/deny rules that run before anything else.

Every utterance passes through `evaluate` before intent routing, tool calls or
any language model. When a rule fires, the reply is the fixed text below; no
model writes or rewrites it. Rules are plain regular expressions so that a
pharmacist can read, challenge and extend them, and so the red-team suite in
`tests/test_safety_policy.py` can pin their behavior.

Order matters: the first matching rule wins, and emergencies are first.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

ALLOW = "allow"
REFUSE = "refuse_and_route"


@dataclass
class Decision:
    action: str
    category: str
    message: str = ""
    route_to: str = ""
    sources: list[dict] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.action == ALLOW


NIA_SAFETY = {
    "source": "National Institute on Aging",
    "title": "Taking Medicines Safely as You Age",
    "url": "https://www.nia.nih.gov/health/medicines-and-medication-management/taking-medicines-safely-you-age",
}
FTC_SCAMS = {
    "source": "Federal Trade Commission",
    "title": "How To Avoid a Scam",
    "url": "https://consumer.ftc.gov/articles/how-avoid-scam",
}

_RULES: list[tuple[str, str, str, str, list[dict]]] = [
    (
        "emergency",
        r"chest pain|can'?t breathe|cannot breathe|trouble breathing|short(ness)? of breath"
        r"|passed out|pass(ing)? out|faint(ed|ing)?|unconscious|stroke|heart attack|seizure"
        r"|severe (pain|bleeding|allergic)|bleeding (a lot|badly|heavily)|throat (is )?(swelling|closing)"
        r"|face (is )?(drooping|numb)|suicid|kill myself|end my life|hurt myself|\boverdos"
        r"|i (fell|have fallen)( down)? and",
        "If this could be an emergency, call 911 now. If you are thinking about harming "
        "yourself, call or text 988. I am not an emergency service and I cannot call for you.",
        "emergency services (911)",
        [],
    ),
    (
        "possible_double_dose",
        r"took (it |them |my \w+ )?(twice|two times|again by mistake|too (much|many))"
        r"|(took|taken|swallowed) (two|2|three|3|double|extra|both)\b"
        r"|double[- ]dosed|accidentally took|took the wrong|wrong (pill|medicine|medication)",
        "I can't tell you whether that is safe. Call your pharmacist or Poison Control at "
        "1-800-222-1222 now; they answer day and night. If you feel unwell, call 911.",
        "pharmacist or Poison Control (1-800-222-1222)",
        [],
    ),
    (
        "missed_dose",
        r"miss(ed|ing)? (a |my |the |one )?(dose|pill|tablet|medicine|medication|\w+ pill)"
        r"|forgot (to take|my (pill|dose|medicine|medication))|(dose|pill) (is |was )?late"
        r"|make up (for )?(a|the|my) (dose|pill)|catch up on",
        "I can't advise on a missed dose. Please don't take extra to make up for it unless "
        "your pharmacist or prescriber tells you to. Call your pharmacist; they can tell you "
        "what to do for this medicine.",
        "pharmacist or prescriber",
        [NIA_SAFETY],
    ),
    (
        "dose_change",
        r"(take|have|use) (an? )?(extra|another|more|less|fewer|half|double|two|2|three|3)\b(?!.*\b(bills?|appointments?|reminders?)\b)"
        r"|double (up|my|the)|(increase|decrease|raise|lower|reduce|change|adjust|up|cut) (my |the )?(dose|dosage)"
        r"|(change|move|shift) (the |my )?(time|schedule) (of|for) my (medicine|medication|pill|dose)"
        r"|(stop|quit) taking|should i (still )?(take|skip|stop)|can i (skip|stop|take it (later|early|now))"
        r"|is it (ok|okay|fine|safe) to (skip|stop|take (it )?(later|early))"
        r"|(split|cut|crush|chew) (my |the |a |this )?(pill|tablet|capsule|medicine|it)\b|how (much|many) should i take",
        "I can't change how or when you take a medicine, or tell you to skip, stop or take "
        "more. Only your prescriber or pharmacist can. I can read you what your verified "
        "record says.",
        "pharmacist or prescriber",
        [NIA_SAFETY],
    ),
    (
        "interaction",
        r"interact|(take|mix|combine|have|drink|use) .*\b(with|together|alongside|same time)\b"
        r"|safe (to take )?with|\b(alcohol|beer|wine|grapefruit|ibuprofen|advil|tylenol|aspirin"
        r"|supplement|vitamin|st\.? john)\b.*\b(with|and|while|on)\b|while (on|taking)\b",
        "I can't tell you whether medicines, foods or drinks are safe together. Please ask "
        "your pharmacist, who can check everything you take. I can show you the official "
        "information for one medicine at a time.",
        "pharmacist",
        [NIA_SAFETY],
    ),
    (
        "symptoms",
        r"side effect|dizz|nause|vomit|threw up|\brash\b|\bitch(y|ing)?\b|\bswell|swoll|headache|light[- ]?headed"
        r"|feel(ing)? (sick|bad|weird|strange|funny|unwell|worse|weak|tired|confused)"
        r"|makes? me feel|allergic|contraindicat|new symptom|is it safe (for me)?|safe for me",
        "I can't judge symptoms or whether a medicine is right for you. Please call your "
        "pharmacist or prescriber about this. If it feels severe or sudden, call 911.",
        "pharmacist or prescriber",
        [NIA_SAFETY],
    ),
    (
        "pill_identification",
        r"what (pill|tablet|medicine|medication) is this|identify (this|the|my) (pill|tablet)"
        r"|which (pill|tablet) is (this|which)|(found|loose|unlabel+ed) (a )?(pill|tablet)"
        r"|(white|pink|blue|round|oval|yellow) (pill|tablet|one)",
        "I cannot confirm which medicine this is. Please check the label with your pharmacist.",
        "pharmacist",
        [],
    ),
    (
        "new_medication",
        r"(add|start|put me on|schedule) (a |my )?(new )?(medicine|medication|prescription|pill)"
        r"|i (was|am|got) (prescribed|given|started on)|my doctor (gave|prescribed|started)",
        "I can't add a medicine or a schedule myself. A pharmacist has to verify the "
        "prescription record first; then I will remind you.",
        "pharmacist verification",
        [],
    ),
    (
        "financial_credentials",
        r"password|passcode|\bpin\b|card number|credit card|debit card|routing number"
        r"|account number|social security|\bssn\b|security code|\bcvv\b|bank login|one[- ]time code",
        "Please don't share passwords, card numbers or account numbers with me. I never "
        "need them and I can't use them.",
        "",
        [],
    ),
    (
        "fraud",
        r"gift cards?|wire (the )?money|western union|bitcoin|crypto|scam|fraud|suspicious"
        r"|(irs|medicare|social security|bank|police|sheriff|grandson|granddaughter) (called|is calling|says|said|needs|texted|emailed)"
        r"|(someone|they|he|she|a man|a woman|caller) (called|says|said|wants|is asking|texted|emailed).*(pay|money|owe|account|arrest)"
        r"|pay (right now|immediately|today) or|shut off (my|the) (power|service) (today|now)",
        "This could be a scam. Don't pay or share any information. Hang up, then call the "
        "company using the phone number printed on your bill. Would you like me to ask a "
        "caregiver you have approved to help?",
        "verified biller contact; approved caregiver",
        [FTC_SCAMS],
    ),
    (
        "payment_request",
        r"\b(pay|send|transfer|wire|move)\b.*(\$\d|\b(bills?|money|dollars|payment|account|balance)\b)"
        r"|make (a|the) payment|pay (it|this|that|them)\b|autopay|set up (a )?payment",
        "I can't pay bills or move money. I can remind you before a bill is due and keep "
        "track of what you tell me you've paid.",
        "",
        [],
    ),
]

_COMPILED = [
    (category, re.compile(pattern, re.IGNORECASE), message, route, sources)
    for category, pattern, message, route, sources in _RULES
]

# "I paid the electric bill" is a self-report, not a request to pay.
_PAID_REPORT = re.compile(r"\b(i|we|i've|i have) (already |just )?paid\b", re.IGNORECASE)


def evaluate(text: str) -> Decision:
    cleaned = " ".join(text.split())
    for category, pattern, message, route, sources in _COMPILED:
        if not pattern.search(cleaned):
            continue
        if category == "payment_request" and _PAID_REPORT.search(cleaned):
            continue
        return Decision(REFUSE, category, message, route, list(sources))
    return Decision(ALLOW, "routine")


# Output guard: last line of defence for anything that is not a fixed template
# (for example, text returned by a public reference source).
_DIRECTIVE = re.compile(
    r"\byou (should|can|may|must|need to) (take|skip|stop|double|increase|decrease|split|crush)\b"
    r"|\bit is safe to\b|\bsafe to (take|combine|mix)\b|\btake (two|2|double|an extra|another)\b",
    re.IGNORECASE,
)


def unsafe_directive(text: str) -> bool:
    """True when text reads like a personal dosing or safety instruction."""
    return bool(_DIRECTIVE.search(text))
