# Medication safety policy

**Review status: written by engineers, not yet reviewed by a pharmacist.**
Pharmacist review of this document, of `services/api/app/ai/safety_policy.py`,
and of the reference cards in `data/synthetic/seed.json` is required before any
session that involves a real medication schedule.

## What CareLens may say about a medicine

1. **The person's own verified record, verbatim.** Name, strength, the
   directions exactly as written on the verified prescription, and the
   scheduled time.
2. **General information from an approved public source**, introduced as
   "general information, not personal medical advice", with the source name,
   title, link and retrieval time. Approved sources: MedlinePlus, DailyMed,
   openFDA. Text that reads like a personal instruction is not relayed.
3. **A referral** to the pharmacist, the prescriber, Poison Control
   (1-800-222-1222) or 911.

Nothing else. In particular CareLens never states or implies that a dose was
swallowed, that a dose was missed, that a combination is safe, or what to do
after a missed or doubled dose.

## Where a schedule can come from

Only from a prescription record that a verifier has confirmed
(`POST /v1/medications/verify`). The voice assistant cannot add, change or
remove a medicine or a time. Reference APIs identify drugs; they are never
read as a prescription.

## Refusal rules

First match wins. Each returns fixed text and runs no tool.

| Category | Examples | Routed to |
|---|---|---|
| emergency | chest pain, can't breathe, stroke, self-harm, overdose | 911, 988 |
| possible_double_dose | "took it twice", "took the wrong pill" | pharmacist, Poison Control |
| missed_dose | "I missed my dose", "forgot my pill" | pharmacist or prescriber |
| dose_change | "can I take two", "stop taking", "change the time", "cut it in half" | pharmacist or prescriber |
| interaction | "with ibuprofen", "while taking", alcohol, grapefruit | pharmacist |
| symptoms | side effects, dizzy, rash, "is it safe for me" | pharmacist or prescriber; 911 if severe |
| pill_identification | "what pill is this" | pharmacist |
| new_medication | "add a new medication", "my doctor prescribed" | pharmacist verification |
| financial_credentials | passwords, card or account numbers | not accepted |
| fraud | gift cards, "the IRS called", pay-now threats | verified biller number, approved caregiver |
| payment_request | "pay my bill", "send $200" | cannot pay; reminders only |

The red-team list that pins these lives in
`services/api/tests/test_safety_policy.py` (42 prompts). It is a starting set,
not a measure of safety.

## Known gaps

- English only; keyword rules miss paraphrases and misheard speech.
- Rules over-refuse some harmless questions. Under-refusal is the risk that
  matters; both need measurement on real utterances.
- No clinically curated interaction data is used or claimed. The NLM RxNav
  interaction API was retired on January 2, 2024.
- Caregiver alerts say a reminder went unanswered. They are not emergency
  alerts and are worded to say so.
