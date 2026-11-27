# ADR 0003: Web stand-in for the phone and the glasses

Status: accepted for the first milestone, 2026-11-23. To be superseded by the
Android app.

## Context

The blueprint's target client is an Android app (Kotlin, Compose) using Meta's
Wearables Device Access Toolkit with MockDeviceKit. That toolchain was not
available where this first build was made, and the backend, safety layer and
caregiver flows could be proven without it.

## Decision

- Build the senior experience as a phone-shaped React page served by the API.
  It uses the browser's speech recognition and synthesis, caches the day plan,
  runs local reminders offline, and queues self-reports.
- Define the device seam now: `WearableAdapter` with `PhoneAdapter` only.
  (A browser imitation of the glasses existed until 2026-11-27 and was removed
  to keep the prototype small; it proved nothing about MockDeviceKit or
  hardware, and the real adapter will be written against the same interface.)

## Consequences

- Demo steps 1 to 6 and 8 run today in a browser. Step 7 (a real MockDeviceKit
  event) is open.
- A browser tab is not a phone: reminders stop when the tab closes, there are
  no system notifications, and background behavior is untested. Android work
  must redo local reminders with AlarmManager/WorkManager.
- The API contract, the adapter contract and the offline rules carry over to
  Android unchanged.
