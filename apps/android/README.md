# Android app: not started

The blueprint's client is a Kotlin + Jetpack Compose app using Meta's Wearables
Device Access Toolkit (DAT) with MockDeviceKit. None of that exists yet. No
Kotlin code is checked in, on purpose: nothing here has been compiled or run.

The web app in `apps/web` stands in for the phone so the backend, safety layer
and caregiver flows could be built and tested first.

## What carries over unchanged

- The HTTP API (`/v1/...`), including `Idempotency-Key` on mutations.
- The device seam in `apps/web/src/wearables/WearableAdapter.ts`:
  `present(speech, display)`, `listen()`, `isConnected()`.
- The offline rules in `apps/web/src/api/offline.ts`: cache the day plan,
  remind locally, queue taps with idempotency keys, do not process voice
  offline.

## Work to do (blueprint ticket CL-009 and part of CL-005)

1. Create the Compose app shell and a typed API client from the OpenAPI
   document at `/openapi.json`.
2. Implement `WearableAdapter` in Kotlin with `PhoneAdapter` (Android
   SpeechRecognizer and TextToSpeech) and `MetaMockAdapter`.
3. Add the Meta DAT Android SDK. Take the version and setup steps from the
   current official README, not from memory:
   https://github.com/meta/meta-wearables-dat-android
4. Pair with MockDeviceKit, exercise at least one supported event, and record
   a reproducible test. Label the recording as simulated.
5. Rebuild local reminders with AlarmManager or WorkManager so they fire with
   the app closed, and test across reboot and Doze.
6. List every real-hardware assumption as untested: audio routing,
   notifications, reconnects, battery, privacy indicators, microphone access.

Do not assume that pairing grants continuous microphone, camera, display or
background alert access.
