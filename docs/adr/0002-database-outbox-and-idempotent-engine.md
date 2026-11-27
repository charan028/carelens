# ADR 0002: Database outbox and an idempotent reminder engine

Status: accepted, 2026-11-23

## Context

Reminders must survive restarts and must not duplicate. In-memory timers lose
work on a crash; a separate job queue adds infrastructure a six-week prototype
does not need.

## Decision

- One function, `engine.tick`, derives everything that is due from the database
  and the clock. It keeps no memory between runs.
- Each notification is a row in `notification_outbox` with a unique
  idempotency key (`dose:<id>:reminder:<n>`), written in the same transaction
  as the state change, then delivered by `outbox.dispatch` with retries.
- Dose events are unique on (schedule, instant).
- Client mutations accept an `Idempotency-Key` header.

## Consequences

- A restart, a double run, or an offline queue replay is harmless.
- Delivery is at-least-once to the adapter; adapters must tolerate a repeat.
- One process runs the loop today. Several API replicas would need a row lock
  or advisory lock around `tick`; the unique keys already prevent duplicates,
  so the cost of skipping that is wasted work, not wrong behavior.

## Database

SQLite for the zero-setup demo, PostgreSQL for anything shared. One set of
SQLAlchemy models and one Alembic history serve both; the test suite runs on
both. `UTCDateTime` keeps timestamps timezone-aware on SQLite.
