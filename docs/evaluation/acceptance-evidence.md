# Acceptance evidence

Each proposed threshold from blueprint section 2, and where the evidence is.
Tests are in `services/api/tests/` unless noted. Results are from synthetic
fixtures on 2026-11-23: 193 backend tests pass on SQLite and on PostgreSQL 16;
13 web tests pass; the browser walk-through in `scripts/e2e_demo.py` passes.

| Area | Criterion | Evidence | Status |
|---|---|---|---|
| Medication schedule | All schedules come from approved structured records; no LLM-generated dose or time | `test_medication_schedule.py`: every dose traces to a verified prescription; unverified record has no schedule; only a verifier can verify; DST gap, repeated hour and one-per-day across a DST change | Met for fixtures |
| Self-report | Every confirmation is labelled self-reported; ingestion never inferred | `test_there_is_no_verified_ingestion_state`, `test_silence_is_tracked_as_unconfirmed...`, `test_system_cannot_mark_a_dose_taken_or_skipped`, `test_self_report_is_labelled_as_self_report` | Met |
| Bill status | Never marked paid because a reminder fired | `test_bills_and_outbox.py`: reminder leaves status unchanged; calendar reaches only open states; `verified_paid` needs a verifying source; voice can only self-report | Met |
| Grounding | Answers carry drug identity, source title and URL, retrieval time; no unsupported personal instruction | `test_demo_story_and_reference.py`: step 3 assertions; cache with source and freshness; stale and bundled labels; unexpected-host link rejected. `test_instruction_like_reference_text_is_never_relayed` | Met for 3 seeded drugs; review set not built |
| Safety | High-risk prompts refuse autonomous dose changes and route to a clinician | `test_safety_policy.py`: 42 prompts refused, no tool call, no state change; forbidden tools unreachable; hostile intent model contained | Met for the starter list; **not pharmacist-reviewed** |
| Reliability | Reminders persist across restarts and reconnects | `test_reminders_persist_across_a_process_restart`, `test_materialize_and_tick_are_idempotent`, outbox retry tests; `apps/web/src/api/offline.test.ts` for queue replay | Met on server; phone side is a browser stand-in, **no device test** |
| Access | Unauthorized caregiver cannot see or modify another user's records | `test_access_control.py`: no token, forged token, wrong role, no grant, scoped grant, revoke, private bills, alert consent, audit entries | Met |
| Access (hosted) | Testers cannot see or affect each other's copy | `test_sandboxes.py`: access code, throttling, forged tokens, data, clock and sign-in isolation, cleanup | Met |
| Wearable | Reproducible simulator test; untested hardware assumptions documented | `apps/web/src/wearables/WearableAdapter.test.ts` covers routing and fallback for the browser stand-in only | **Not met.** No MockDeviceKit run exists |
| Accessibility | Large text, simple controls, voice alternatives evaluated with consenting users | Hyperlegible typeface, 48 px targets, typed and tapped alternatives, quiet mode, reduced-motion support are built | **Not met.** No user evaluation; no WCAG audit |

## Not measured

Speech recognition accuracy, intent accuracy on real speech, drug-name
normalization precision, reminder latency on a device, and anything about
older adults' success with the product. The blueprint asks for these targets
to be set after a representative evaluation set exists; none exists yet.

## Reference APIs

Response shapes for RxNorm properties, MedlinePlus Connect and DailyMed were
checked against the live services on 2026-11-23 and the adapters are tested
against those shapes. The adapters were not run live from the build
environment, and openFDA's shape was not re-checked that day.
