"""Assistant orchestration: policy gate, intent, allow-listed tool, template.

    utterance -> safety_policy.evaluate -> intent model -> tools.call -> reply

Every sentence the assistant can say is either a fixed template in this file
filled with fields from verified records, or a labelled quotation from a
public source. No model generates reply text, doses, times or payment facts.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from .. import clock
from ..models import BillStatus, DoseStatus, User
from ..services import audit
from ..services.medication import education, scheduler
from ..services.notifications.engine import fmt_date, fmt_time
from . import safety_policy, tools
from .intent import Intent, IntentModel, RuleBasedIntentModel

MAX_UTTERANCE_CHARS = 400
UNKNOWN_MEDICINE = "I cannot confirm which medicine this is. Please check the label with your pharmacist."
CAPABILITIES = (
    "I can tell you what is on your list today, read what a medicine is generally used "
    "for, note when you say you took a medicine, and remind you before a bill is due."
)

_intent_model: IntentModel = RuleBasedIntentModel()


def set_intent_model(model: IntentModel) -> None:
    global _intent_model
    _intent_model = model


@dataclass
class Reply:
    text: str
    intent: str
    display: str = ""
    policy: dict = field(default_factory=lambda: {"action": "allow", "category": "routine"})
    sources: list = field(default_factory=list)
    focus: dict | None = None
    options: list = field(default_factory=list)
    tool_calls: list = field(default_factory=list)
    changed: bool = False

    def to_dict(self) -> dict:
        data = asdict(self)
        # The on-screen watermark already marks demo data; don't read it aloud.
        data["text"] = self.text.replace(" (synthetic)", "")
        data["display"] = (self.display or _shorten(data["text"])).replace(" (synthetic)", "")
        return data


def _shorten(text: str, limit: int = 90) -> str:
    first = re.split(r"(?<=[.?!])\s", text.strip())[0]
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _greeting(user: User) -> str:
    hour = clock.now().astimezone(ZoneInfo(user.timezone)).hour
    part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
    return f"Good {part}, {user.display_name.split()[0]}."


def _mentioned(text: str, doses: list[dict]) -> list[dict]:
    lowered = text.lower()
    return [dose for dose in doses if dose["name"].lower() in lowered]


def _focus(text: str, context: dict, doses: list[dict]) -> tuple[list[dict], bool]:
    """Which dose is the person talking about? Returns (doses, was_ambiguous)."""
    named = _mentioned(text, doses)
    awaiting = [dose for dose in doses if dose["status"] in DoseStatus.AWAITING]
    if named:
        pending = [dose for dose in named if dose["status"] in DoseStatus.AWAITING]
        return (pending or named)[:1], False
    remembered = [dose for dose in doses if dose["dose_event_id"] == context.get("focus_dose_event_id")]
    if remembered:
        return remembered, False
    due = [dose for dose in awaiting if dose["status"] in (DoseStatus.REMINDED, DoseStatus.OVERDUE)]
    if not due:
        horizon = (clock.now() + timedelta(hours=2)).isoformat()
        due = [dose for dose in awaiting if dose["scheduled_at"] <= horizon]
    if len(due) > 1 and not re.search(r"\b(them|all|both|everything)\b", text.lower()):
        return due, True
    return due, False


def _match_bill(text: str, bills: list[dict]) -> list[dict]:
    lowered = text.lower().replace("electricity", "electric").replace("power", "electric")
    open_bills = [bill for bill in bills if bill["status"] in BillStatus.OPEN]
    matches = []
    for bill in open_bills:
        words = [w for w in re.findall(r"[a-z]+", bill["payee_label"].lower()) if len(w) > 3 and w != "synthetic"]
        if any(word in lowered for word in words):
            matches.append(bill)
    if not matches and len(open_bills) == 1:
        return open_bills
    return matches


def _dose_line(dose: dict) -> str:
    return (
        f"At {dose['local_time']}: {dose['name']} {dose['strength']}. "
        f"Your verified record says: \"{dose['directions']}\""
    )


# --- intent handlers ---------------------------------------------------------


def _today(db: Session, user: User, intent: Intent, text: str, context: dict) -> Reply:
    data = tools.call("get_today_tasks", db, user)
    period = intent.slots.get("period")
    doses = [d for d in data["medications"] if d["status"] in DoseStatus.AWAITING]
    if period:
        doses = [d for d in doses if d["period"] == period]
    bills = [b for b in data["bills"] if b["status"] in BillStatus.OPEN and b["days_until_due"] <= 7]
    appointments = [a for a in data["appointments"] if a["date_label"] == data["date_label"]]
    scope = f"this {period}" if period in ("morning", "evening") else "today"
    parts = [_greeting(user)]
    if not (doses or bills or appointments):
        parts.append(f"Nothing is waiting for you {scope}.")
    for dose in doses:
        parts.append(_dose_line(dose))
    for bill in bills:
        amount = f" of {bill['amount']}" if bill["amount"] else ""
        when = (
            "is due today" if bill["days_until_due"] == 0
            else f"was due {bill['due_label']}" if bill["days_until_due"] < 0
            else f"is due {bill['due_label']}"
        )
        parts.append(f"Your {bill['payee_label']} bill{amount} {when}.")
    for appointment in appointments:
        parts.append(f"{appointment['title']} is today at {appointment['local_time']}.")
    done = [d for d in data["medications"] if d["status"] == DoseStatus.SELF_REPORTED_TAKEN]
    if done and not period:
        parts.append(f"You told me you took {_join([d['name'] for d in done])}.")
    focus = doses[0] if len(doses) == 1 else None
    counts = []
    if doses:
        counts.append(f"{len(doses)} medicine{'s' if len(doses) != 1 else ''}")
    if bills:
        counts.append(f"{len(bills)} bill{'s' if len(bills) != 1 else ''}")
    return Reply(
        text=" ".join(parts),
        display=(f"{scope.capitalize()}: " + _join(counts)) if counts else f"Nothing waiting {scope}",
        intent=intent.name,
        focus=_focus_dict(focus),
        tool_calls=["get_today_tasks"],
    )


def _focus_dict(dose: dict | None) -> dict | None:
    if not dose:
        return None
    return {"dose_event_id": dose["dose_event_id"], "rxcui": dose["rxcui"], "name": dose["name"]}


def _list_medications(db: Session, user: User, intent: Intent, text: str, context: dict) -> Reply:
    medications = tools.call("get_verified_medications", db, user)
    if not medications:
        return Reply("You have no verified medicines on your list yet.", intent.name)
    lines = [
        f"{m['name']} {m['strength']}: \"{m['directions']}\"" for m in medications
    ]
    return Reply(
        text=f"Your verified list has {len(medications)} medicine"
        f"{'s' if len(medications) != 1 else ''}. " + " ".join(lines),
        display=_join([m["name"] for m in medications]),
        intent=intent.name,
        tool_calls=["get_verified_medications"],
    )


def _education(db: Session, user: User, intent: Intent, text: str, context: dict) -> Reply:
    data = tools.call("get_today_tasks", db, user)
    mine = tools.call("get_verified_medications", db, user)
    calls = ["get_today_tasks", "get_verified_medications"]
    lowered = text.lower()
    rxcui, focus = None, None

    named = [m for m in mine if m["name"].lower() in lowered or any(b.lower() in lowered for b in m["brand_names"])]
    if named:
        rxcui = named[0]["rxcui"]
        focus = next((d for d in data["medications"] if d["rxcui"] == rxcui), None)
    elif re.search(r"\b(this|that|it|my) ?(medicine|medication|pill|tablet|one)?\b", lowered) and not _typed_name(lowered):
        doses, ambiguous = _focus(text, context, data["medications"])
        if ambiguous or (not doses and len(mine) > 1):
            options = [d["name"] for d in doses] if doses else [m["name"] for m in mine]
            return Reply(
                f"Which medicine do you mean: {_join(sorted(set(options)))}?",
                intent.name, options=sorted(set(options)), tool_calls=calls,
            )
        if doses:
            rxcui, focus = doses[0]["rxcui"], doses[0]
        elif len(mine) == 1:
            rxcui = mine[0]["rxcui"]
    if rxcui is None:
        query = _typed_name(lowered)
        if not query:
            return Reply(UNKNOWN_MEDICINE, intent.name, tool_calls=calls)
        found = education.search_drugs(db, query)
        if found["not_found"]:
            return Reply(UNKNOWN_MEDICINE, intent.name, tool_calls=calls)
        if found["needs_disambiguation"]:
            options = [c["name"] for c in found["candidates"][:4]]
            return Reply(
                f"I found more than one match for {query}: {_join(options)}. "
                "Which one is on your label? Your pharmacist can help you check.",
                intent.name, options=options, tool_calls=calls,
            )
        rxcui = found["match"]["rxcui"]

    info = tools.call("get_drug_education", db, user, rxcui=rxcui)
    calls.append("get_drug_education")
    if info is None or not info["sources"]:
        return Reply(
            "I don't have an approved information source for that medicine yet. "
            "Your pharmacist can tell you about it.",
            intent.name, tool_calls=calls,
        )
    primary = info["sources"][0]
    drug = info["drug"]
    summary = primary["summary"]
    if summary and safety_policy.unsafe_directive(summary):
        summary = ""  # never relay text that reads like a personal instruction
    parts = [f"{drug['generic_name'].capitalize()} {drug['strength']}.", info["notice"]]
    parts.append(
        f"According to {primary['source']}: {summary}" if summary
        else f"{primary['source']} has a page about it that I can show you."
    )
    parts.append(f"Source: {primary['source']}, \"{primary['title']}\".")
    if primary["origin"] == "bundled":
        parts.append("I could not reach the live source, so this is my saved reference card.")
    elif primary["stale"]:
        parts.append("I could not refresh this just now, so it may be out of date.")
    parts.append(info["ask"])
    return Reply(
        text=" ".join(parts),
        display=f"{drug['generic_name']}: general info from {primary['source']}",
        intent=intent.name,
        sources=info["sources"],
        focus=_focus_dict(focus),
        tool_calls=calls,
    )


def _typed_name(lowered: str) -> str:
    """Pull a medicine name out of a question, if one was said."""
    match = re.search(
        r"(?:what(?:'s| is| are| does)|tell me (?:more )?about|why (?:do i|am i) tak\w*)\s+(.+?)"
        r"(?:\s+(?:used|for|do|treat|good)\b.*|\?|$)",
        lowered,
    )
    if not match:
        return ""
    name = re.sub(r"\b(this|that|it|my|the|a|an|medicine|medication|pill|tablet|one|me)\b", " ", match.group(1))
    return " ".join(name.split())


def _report(status: str):
    past = {"taken": "took", "skipped": "skipped", "unsure": "are not sure about"}[status]

    def handler(db: Session, user: User, intent: Intent, text: str, context: dict) -> Reply:
        data = tools.call("get_today_tasks", db, user)
        doses, ambiguous = _focus(text, context, data["medications"])
        calls = ["get_today_tasks"]
        if ambiguous:
            options = sorted({d["name"] for d in doses})
            return Reply(
                f"Which one do you mean: {_join(options)}? You can also say \"all of them\".",
                intent.name, options=options, tool_calls=calls,
            )
        if not doses:
            return Reply(
                "I don't see a medicine waiting for you right now, so I haven't recorded anything.",
                intent.name, tool_calls=calls,
            )
        updated = []
        for dose in doses:
            updated.append(
                tools.call("record_dose_self_report", db, user, dose_event_id=dose["dose_event_id"], status=status)
            )
            calls.append("record_dose_self_report")
        names = _join([f"{d['name']} {d['strength']}" for d in updated])
        now_label = fmt_time(clock.now(), user.timezone)
        if status == "taken":
            sentence = f"Okay. At {now_label} you told me you {past} your {names}. I've saved that as your own report."
        elif status == "skipped":
            sentence = (
                f"Okay. I've noted that you {past} your {names}. If you're unsure what to do next, "
                "your pharmacist can tell you."
            )
        else:
            sentence = (
                f"Okay. I've marked your {names} as unconfirmed. I can't tell you whether to take it now; "
                "your pharmacist can."
            )
        return Reply(
            text=sentence,
            display=f"Saved: you said you {past} {_join([d['name'] for d in updated])}",
            intent=intent.name,
            focus=_focus_dict(updated[0]),
            tool_calls=calls,
            changed=True,
        )

    return handler


def _list_bills(db: Session, user: User, intent: Intent, text: str, context: dict) -> Reply:
    bills = [b for b in tools.call("get_bill_due_dates", db, user) if b["status"] in BillStatus.OPEN]
    if not bills:
        return Reply("You have no open bills on your list.", intent.name, tool_calls=["get_bill_due_dates"])
    lines = []
    for bill in bills:
        amount = f", {bill['amount']}," if bill["amount"] else ""
        lines.append(f"{bill['payee_label']}{amount} due {bill['due_label']}.")
    return Reply(
        text=f"You have {len(bills)} open bill{'s' if len(bills) != 1 else ''}. " + " ".join(lines)
        + " I can remind you, but I cannot pay them.",
        display=f"{bills[0]['payee_label']} due {bills[0]['due_label']}",
        intent=intent.name,
        tool_calls=["get_bill_due_dates"],
    )


def _bill_reminder(db: Session, user: User, intent: Intent, text: str, context: dict) -> Reply:
    bills = tools.call("get_bill_due_dates", db, user)
    calls = ["get_bill_due_dates"]
    matches = _match_bill(text, bills)
    if len(matches) != 1:
        options = [b["payee_label"] for b in bills if b["status"] in BillStatus.OPEN]
        if not options:
            return Reply(
                "I don't have that bill on your list. You can add it on the Bills screen with its due date.",
                intent.name, tool_calls=calls,
            )
        return Reply(f"Which bill do you mean: {_join(options)}?", intent.name, options=options, tool_calls=calls)
    bill = matches[0]
    lead = int(intent.slots.get("lead_days", 1))
    now = clock.now()
    due = date.fromisoformat(bill["due_date"])
    remind_at = scheduler.local_to_utc(due - timedelta(days=lead), "09:00", user.timezone)
    if remind_at <= now:
        tomorrow = scheduler.local_day(now, user.timezone) + timedelta(days=1)
        remind_at = scheduler.local_to_utc(min(tomorrow, due), "09:00", user.timezone)
        if remind_at <= now:
            remind_at = now + timedelta(minutes=1)
    tools.call("create_bill_reminder", db, user, bill_id=bill["id"], reminder_at=remind_at)
    calls.append("create_bill_reminder")
    remind_day = fmt_date(remind_at.astimezone(ZoneInfo(user.timezone)).date())
    return Reply(
        text=f"Okay. I'll remind you about your {bill['payee_label']} bill on {remind_day} at "
        f"{fmt_time(remind_at, user.timezone)}. It is due {bill['due_label']}. "
        "I can remind you, but I cannot pay it.",
        display=f"Reminder set: {bill['payee_label']}, {remind_day}",
        intent=intent.name,
        tool_calls=calls,
        changed=True,
    )


def _bill_paid(db: Session, user: User, intent: Intent, text: str, context: dict) -> Reply:
    bills = tools.call("get_bill_due_dates", db, user)
    calls = ["get_bill_due_dates"]
    matches = _match_bill(text, bills)
    if len(matches) != 1:
        options = [b["payee_label"] for b in bills if b["status"] in BillStatus.OPEN]
        if not options:
            return Reply("I don't see an open bill to mark.", intent.name, tool_calls=calls)
        return Reply(f"Which bill did you pay: {_join(options)}?", intent.name, options=options, tool_calls=calls)
    bill = matches[0]
    tools.call("record_bill_self_report", db, user, bill_id=bill["id"])
    calls.append("record_bill_self_report")
    return Reply(
        text=f"Okay. I've noted that you told me you paid your {bill['payee_label']} bill. "
        "I haven't checked this with the company.",
        display=f"Saved: you said you paid {bill['payee_label']}",
        intent=intent.name,
        tool_calls=calls,
        changed=True,
    )


def _appointments(db: Session, user: User, intent: Intent, text: str, context: dict) -> Reply:
    data = tools.call("get_today_tasks", db, user)
    items = data["appointments"]
    if not items:
        return Reply("You have no appointments in the next week.", intent.name, tool_calls=["get_today_tasks"])
    lines = [f"{a['title']}, {a['date_label']} at {a['local_time']}" + (f", {a['location']}" if a["location"] else "") + "." for a in items]
    return Reply(
        text="Coming up: " + " ".join(lines),
        display=f"{items[0]['date_label']}, {items[0]['local_time']}: appointment",
        intent=intent.name,
        tool_calls=["get_today_tasks"],
    )


def _caregiver_alert(db: Session, user: User, intent: Intent, text: str, context: dict) -> Reply:
    task = context.get("focus_dose_event_id") or "general"
    result = tools.call("request_caregiver_alert", db, user, task_id=task, reason="asked by voice")
    if not result["notified"]:
        text_out = (
            "You haven't approved anyone to get alerts from me, so I haven't told anyone. "
            "You can choose who on the Sharing screen. If this is an emergency, call 911."
        )
    else:
        text_out = (
            f"Okay. I've sent {_join(result['notified'])} a note asking them to check in with you. "
            "I can't tell whether they have seen it. If this is an emergency, call 911."
        )
    return Reply(
        text_out, intent.name, tool_calls=["request_caregiver_alert"], changed=True,
        display="Check-in note sent" if result["notified"] else "No one is approved for alerts",
    )


def _help(db: Session, user: User, intent: Intent, text: str, context: dict) -> Reply:
    return Reply(CAPABILITIES, intent.name, display="Ask: what should I do today?")


def _unknown(db: Session, user: User, intent: Intent, text: str, context: dict) -> Reply:
    return Reply("I didn't catch that. " + CAPABILITIES, intent.name, display="Sorry, I didn't catch that")


HANDLERS = {
    "today_briefing": _today,
    "list_medications": _list_medications,
    "medication_education": _education,
    "report_taken": _report("taken"),
    "report_skipped": _report("skipped"),
    "report_unsure": _report("unsure"),
    "list_bills": _list_bills,
    "create_bill_reminder": _bill_reminder,
    "report_bill_paid": _bill_paid,
    "list_appointments": _appointments,
    "request_caregiver_alert": _caregiver_alert,
    "help": _help,
    "unknown": _unknown,
}


def handle(db: Session, user: User, text: str, context: dict | None = None) -> dict:
    context = context or {}
    text = " ".join(text.split())[:MAX_UTTERANCE_CHARS]

    decision = safety_policy.evaluate(text)
    if not decision.allowed:
        reply = Reply(
            text=decision.message,
            intent="safety_refusal",
            policy={"action": decision.action, "category": decision.category, "route_to": decision.route_to},
            sources=decision.sources,
        )
    else:
        intent = _intent_model.classify(text)
        if intent.name == "repeat" and context.get("last_reply"):
            reply = Reply(str(context["last_reply"])[:1200], "repeat")
        else:
            handler = HANDLERS.get(intent.name, _unknown)
            reply = handler(db, user, intent, text, context)

    # Intent and policy category only. The utterance itself is never logged.
    audit.record(
        db,
        actor_id=user.id,
        subject_user_id=user.id,
        action="assistant.message",
        resource_type="assistant",
        detail={"intent": reply.intent, "policy": reply.policy["category"], "tools": reply.tool_calls},
        outcome="refused" if reply.intent == "safety_refusal" else "ok",
    )
    db.commit()
    return reply.to_dict()
