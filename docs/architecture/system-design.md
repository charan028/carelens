# System design

## The one rule

Voice and any language model are an **interface to restricted tools**. Facts
about prescriptions, dose times, completion, payment status, permissions and
notification delivery are owned by deterministic services and the database.
Nothing a person says, and nothing a model returns, can create or change those
facts except through a small set of reviewed functions.

## Shape

```text
 Browser (apps/web)                               FastAPI (services/api)
┌────────────────────────────┐            ┌──────────────────────────────────────┐
│ Senior phone UI            │   HTTPS    │ api/v1 routes  ── auth + role checks │
│  voice in/out, taps        │◀──────────▶│      │                               │
│  OfflineStore + queue      │            │      ▼                               │
│  phone-local reminders     │            │ ai/assistant                         │
│ WearableAdapter            │            │   1. safety_policy.evaluate  (rules) │
│  └ PhoneAdapter            │            │   2. intent model     (closed list)  │
│                            │            │   3. tools.call       (allow-list)   │
│ Caregiver dashboard        │            │   4. fixed reply template            │
│ Pharmacist queue           │            │      │                               │
└────────────────────────────┘            │      ▼                               │
                                          │ services/                            │
                                          │   medication  scheduler, states      │
                                          │   billing     states, recurrence     │
                                          │   caregiver   consent, scopes        │
                                          │   today       read models            │
                                          │   notifications                      │
                                          │     engine.tick ──▶ outbox ──▶ deliver
                                          │      │                               │
                                          │      ▼                               │
                                          │ PostgreSQL / SQLite  (+ audit_events)│
                                          │                                      │
                                          │ integrations/  RxNorm · MedlinePlus  │
                                          │                DailyMed · openFDA    │
                                          │   read-only, cached, never a source  │
                                          │   of prescription or schedule facts  │
                                          └──────────────────────────────────────┘
```

## How an utterance is handled

1. **Policy gate.** `safety_policy.evaluate(text)` runs ordered regular-expression
   rules. Emergencies, possible double doses, missed doses, dose changes,
   interactions, symptoms, pill identification, unverified additions,
   credentials, fraud and payment requests each return a fixed message that
   routes to a person or service. No tool runs and nothing is written.
2. **Intent.** A classifier picks one of fourteen intents. The default is
   rule-based. `ConstrainedLLMIntentModel` lets an LLM do this step, and can
   only return a name from the same list; anything else falls back to rules.
3. **Tool.** The handler calls `tools.call(name, ...)`. The registry holds
   eight functions. The five functions the blueprint forbids are named in
   `DISALLOWED` and tested as unreachable. Tools take the signed-in user from
   the request, never from the text.
4. **Reply.** A template in `assistant.py` is filled from verified records.
   Directions are quoted verbatim from the verified prescription. Education
   text is a labelled quotation from a public source and is dropped if it reads
   like a personal instruction.

The audit log records the intent and policy category of each message, never
the words.

## Time-driven behavior

`engine.tick(db, now)` is the only place time moves state. It is idempotent
and stateless, so it can run on a timer, after a restart, or twice at once:

- creates dose events for today and tomorrow from verified schedules
  (unique on schedule + instant);
- sends up to three reminders, 15 minutes apart, through the outbox with keys
  like `dose:<id>:reminder:2`;
- moves `scheduled → reminded → overdue → unknown` (60 minutes, then 4 hours);
- queues a caregiver note after the number of unanswered reminders the older
  adult chose, only to caregivers with the medicines scope and alerts on;
- moves open bills between `upcoming`, `due` and `overdue` by the calendar and
  sends one reminder per bill. It cannot mark a bill paid.

All time comes from `clock.now()`, which tests and the demo can move.

## Dose and bill states

```text
dose:  scheduled ─▶ reminded ─▶ overdue ─▶ unknown          (system, by time)
           └────────────┴──────────┴──────────┴─▶ self_reported_taken | skipped | unknown
                                                              (person's own word; corrections allowed)

bill:  upcoming ─▶ due ─▶ overdue                           (system, by calendar)
           └────────┴───────┴─▶ self_reported_paid ─▶ verified_paid
                                  (person's word)       (needs biller confirmation or statement)
```

## Access model

- Roles: senior, caregiver, verifier. Each route declares its role.
- A caregiver reads through exactly one function, `today.caregiver_view`, which
  filters by the scopes on an active grant. Private bills are always hidden.
- Changing scopes replaces the grant, so the history of what was shared stays.
- Missing and not-yours return the same 404, so ids cannot be probed.

## Offline and wearable behavior

- The phone caches the last day plan and schedules reminders from it locally.
- Taps made offline go into a queue with idempotency keys and replay on
  reconnect; the server applies each once.
- Voice requests are not processed offline, because the safety gate lives on
  the server. The phone says so.
- All output goes through the `WearableAdapter` seam; today the only adapter is
  the phone. The phone screen always shows the full reply.

## Choices worth knowing

See [docs/adr](../adr). In short: rules before models; SQLite for the demo and
PostgreSQL for anything shared, behind one SQLAlchemy model set; a database
outbox instead of in-memory timers; a web stand-in for the phone until the
Android app exists.
