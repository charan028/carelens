# ADR 0001: Deterministic core, rules before models

Status: accepted, 2026-11-23

## Context

The product talks about medicines and money with older adults. A fabricated
dose, a wrong "paid" status, or an invented reassurance about a drug
combination would cause real harm, and a language model can produce all three.

## Decision

- Schedules, states, permissions and payment status are computed by ordinary
  code from database records. No model output is ever written to them.
- Every utterance passes a rule-based safety policy before anything else.
  Refusals are fixed text.
- A model, when one is added, may only choose an intent from a closed list.
  Replies are templates filled from verified records.

## Consequences

- The assistant is narrow and sometimes stiff. It refuses cautiously phrased
  but harmless questions (for example anything containing "with" near a
  medicine word). That trade is intended for an MVP.
- Rule coverage is only as good as the red-team list. It needs pharmacist
  review and real utterances from usability sessions.
- Behavior is testable line by line, which is what the acceptance thresholds
  ask for.

## Alternatives considered

- LLM with tool calling and a system prompt as the guard: rejected for the MVP;
  the guard would be probabilistic and hard to prove.
- LLM-written replies checked by an output filter: rejected; filters miss
  paraphrases.
