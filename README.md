# CareLens

A voice-first assistant for older adults: verified medication reminders, general
medication education from public sources, bill reminders, self-reported
confirmations, and a caregiver view the older adult controls.

**Status: working proof of concept, synthetic data only.** It is not a medical
device, not an emergency service, and it never pays bills. No real glasses and
no Meta SDK have been tested (see [What is not built](#what-is-not-built)).

## Run the demo

Needs Python 3.12+ (and Node 20+ only if `apps/web/dist` is missing).

```bash
./scripts/run_demo.sh        # then open http://localhost:8000
```

The first run creates a virtualenv, a local SQLite file and the synthetic
records. Nothing leaves your machine except optional lookups to public NLM/FDA
reference APIs (turn off with `CARELENS_LIVE_REFERENCE_APIS=0`).

To let other people test it on a public link, behind an access code and with a
private copy per tester, see [docs/hosting.md](docs/hosting.md). After
`scripts/configure_hosted.py`, `./scripts/run_demo.sh --hosted` tries that mode
locally and prints the access code; without the flag the demo is always local.

Then follow [demos/demo-script.md](demos/demo-script.md): it walks the eight
steps of the blueprint's demo story in about five minutes. Voice input uses the
browser's speech recognition (Chrome or Edge); every voice action also has a
typed or tapped alternative.

## What works

| Blueprint area | In this repo |
|---|---|
| Medication schedule | Deterministic, DST-safe scheduling from pharmacist-verified records only. Unverified records schedule nothing. |
| Dose states | `scheduled`, `reminded`, `overdue`, `unknown`, `self_reported_taken`, `skipped`, `discontinued`. No "taken" state exists; silence becomes "unconfirmed", never "missed". |
| Bills | Manual entry, recurrence, reminders. A reminder never changes payment status. "I paid" is kept apart from a payment confirmed by a record. No credentials, no payments. |
| Education | RxNorm, MedlinePlus Connect, DailyMed and openFDA adapters with a 24-hour cache, source link, retrieval time, stale label and a bundled fallback card. |
| Assistant | Safety policy gate, closed intent list, eight allow-listed tools, fixed reply templates. No language model writes replies, doses, times or payment facts. |
| Caregiver | Per-category, revocable consent; alerts only to people the older adult approved; every view and permission change audited. |
| Reliability | Durable outbox with idempotency keys and retries; reminders survive restarts; phone-local reminders and a self-report queue when offline. |
| Hosted demo | Access code, a private sandbox per tester (own records, own clock), idle cleanup, refusal to start with weak settings. |
| Wearable seam | `WearableAdapter` interface with a phone adapter and a browser stand-in for glasses, including fallback to the phone on disconnect. |

Evidence for each acceptance threshold is mapped to tests in
[docs/evaluation/acceptance-evidence.md](docs/evaluation/acceptance-evidence.md).

## What is not built

Stated plainly, because a truthful baseline is the point of this milestone.

- **Android app and Meta MockDeviceKit.** Not started. The web app stands in
  for the phone, and `SimulatedGlassesAdapter` is a browser model, **not**
  Meta's simulator. Blueprint demo step 7 and Milestone 1's MockDeviceKit item
  are therefore **not met**. See [apps/android/README.md](apps/android/README.md).
- **Language model.** The intent classifier is rule-based. The adapter seam for
  a constrained LLM exists and is tested against a hostile fake, but no provider
  is wired in.
- **Pharmacist review.** The safety rules and the three bundled reference cards
  were written by engineers. They need pharmacist review before any session
  with real medication schedules.
- **Real sign-in.** Demo personas use signed tokens with no passwords. The
  `/v1/dev/*` endpoints are open in demo mode and disabled otherwise.
- **Push delivery.** Notifications are delivered in-app by polling. FCM/APNs,
  SMS and email adapters plug into the outbox but do not exist yet.
- **Encryption at rest, retention policy, rate limiting across nodes.** Not
  implemented; required before any pilot with real people.
- **Usability evidence.** No older adult has used this. No performance or
  outcome claims are made.
- **Docker and deployment.** The Dockerfile, Compose file and `render.yaml` are
  written but the image was not built and nothing was deployed where this repo
  was made. Hosted mode itself was run and tested on PostgreSQL.
- **Grant documents.** Not drafted here.

Known behavior to be aware of: a self-report made offline is stamped with the
time it syncs, not the time it was tapped.

## Tests

```bash
cd services/api && pip install -r requirements-dev.txt && python -m pytest -q   # 193 tests
cd apps/web && npm install && npm test                                           # 13 tests
python scripts/e2e_demo.py      # browser walk-through; needs the demo running and Playwright
```

The backend suite also passes on PostgreSQL 16
(`CARELENS_TEST_DATABASE_URL=postgresql+psycopg://... python -m pytest`), and the
Alembic migration applies cleanly there with no drift from the models.

## Layout

```text
services/api/app/
  main.py                  FastAPI app; serves the built web app too
  api/v1/                  HTTP routes (senior.py, shared.py)
  auth/                    demo tokens and role checks
  sandbox.py               private copy per tester for the hosted demo
  models/                  the twelve entities from blueprint section 6
  services/medication/     scheduler, dose state machine, education + cache
  services/billing/        bill state machine and recurrence
  services/notifications/  durable outbox and the reminder engine
  services/caregiver/      consent grants and access checks
  integrations/            rxnorm, medlineplus, dailymed, openfda
  ai/                      safety_policy, intent, tools, assistant
  migrations/              Alembic
apps/web/src/
  pages/                   senior phone, caregiver dashboard, pharmacist queue
  wearables/               WearableAdapter, phone and simulated glasses
  api/                     client, offline store and queue
render.yaml                hosted demo blueprint (see docs/hosting.md)
data/synthetic/seed.json   every fictional person and record
docs/                      system design, ADRs, safety policy, evidence map
```

Design and reasoning: [docs/architecture/system-design.md](docs/architecture/system-design.md).
The project blueprint this implements lives in the "caredlens" Claude project
as `CareLens_Project_Blueprint.md`.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `CARELENS_ENV` | `demo` | `demo` enables demo sign-in, auto-seed and `/v1/dev/*`. |
| `CARELENS_DATABASE_URL` | SQLite file in the repo root | Any PostgreSQL URL, including Supabase (see docs/hosting.md). |
| `CARELENS_SECRET_KEY` | demo value | Signs tokens. Set a real one outside demos. |
| `CARELENS_LIVE_REFERENCE_APIS` | `1` | `0` uses cache and bundled cards only. |
| `CARELENS_RUN_ENGINE` | `1` | Background reminder loop. |
| `CARELENS_SANDBOXES` | `0` | `1` for a hosted demo: access code and a private copy per tester. |
| `CARELENS_ACCESS_CODE` | none | Required when sandboxes are on. |
