"""Claude as intent classifier: one schema-bound answer (intent plus a few
details) crosses the seam, every field is validated, and the rules take over
whenever the model is unavailable or answers off-list.
No network: the SDK client is a fake."""
import json

from app.ai import assistant, llm
from app.ai.intent import INTENTS, ConstrainedLLMIntentModel, RuleBasedIntentModel

from .conftest import say


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Response:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [_Block(text)]
        self.stop_reason = stop_reason


class _Messages:
    def __init__(self, answer):
        self.answer = answer  # dict (serialized to JSON) or raw string
        self.stop_reason = "end_turn"
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = self.answer if isinstance(self.answer, str) else json.dumps(self.answer)
        return _Response(text, self.stop_reason)


class _FakeClient:
    def __init__(self, answer):
        self.messages = _Messages(answer)


def _answer(intent, medicine=None, period=None, lead_days=None):
    return {"intent": intent, "medicine": medicine, "period": period, "lead_days": lead_days}


def test_completer_sends_only_the_utterance_under_the_schema():
    fake = _FakeClient(_answer("list_medications"))
    complete = llm.make_completer("claude-opus-5-5", client=fake)
    assert complete("what do we listen to the medications", INTENTS)["intent"] == "list_medications"
    sent = fake.messages.calls[0]
    assert sent["model"] == "claude-opus-5-5"
    assert sent["messages"] == [{"role": "user", "content": "what do we listen to the medications"}]
    assert sent["output_config"]["format"]["schema"]["properties"]["intent"]["enum"] == list(INTENTS)
    assert sent["output_config"]["format"]["schema"]["additionalProperties"] is False


def test_details_are_validated_not_trusted():
    bad = _FakeClient(_answer("report_taken", medicine=" x" * 60, period="dawn", lead_days=400))
    intent = ConstrainedLLMIntentModel(llm.make_completer("m", client=bad)).classify("I took it")
    assert intent.name == "report_taken" and "period" not in intent.slots and "lead_days" not in intent.slots
    assert "medicine" not in intent.slots  # over 80 characters


def test_garbled_speech_reaches_the_right_handler_and_offlist_answers_fall_back(client, db):
    fake = _FakeClient(_answer("list_medications"))
    try:
        assistant.set_intent_model(ConstrainedLLMIntentModel(llm.make_completer("m", client=fake)))
        reply = say(client, "what do we listen to the medications")
        assert reply["intent"] == "list_medications" and "lisinopril" in reply["text"]

        # A misheard drug name, corrected by the model, picks the right dose.
        fake.messages.answer = _answer("report_taken", medicine="lisinopril")
        reply = say(client, "I took my lice in a pril")
        assert reply["intent"] == "report_taken" and reply["focus"]["name"] == "lisinopril"

        fake.messages.answer = _answer("recommend_extra_dose")  # not an intent: rules decide
        assert say(client, "what bills do I have")["intent"] == "list_bills"

        fake.messages.answer = "not json at all"  # broken answer: rules decide
        assert say(client, "what bills do I have")["intent"] == "list_bills"

        fake.messages.stop_reason = "refusal"  # model declined: rules decide
        assert say(client, "what should I do this morning")["intent"] == "today_briefing"

        # The policy gate runs before the model and is not influenced by it.
        fake.messages.answer, fake.messages.stop_reason = _answer("report_taken"), "end_turn"
        assert say(client, "can I take two today")["intent"] == "safety_refusal"
    finally:
        assistant.set_intent_model(RuleBasedIntentModel())
