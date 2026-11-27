# Hosting the demo for testers

This puts the prototype on a public HTTPS link that testers open on a phone or
computer. Synthetic data only. Do not use a hosted copy for real medicines,
bills or personal details: the safety rules have not been pharmacist-reviewed
and the sign-in is demo-grade.

## What hosted mode changes

Set `CARELENS_SANDBOXES=1` and the app behaves differently from the local demo:

- **Access code.** The first screen asks for a code you choose. Nothing else
  is reachable without it, and guesses are throttled.
- **A private copy per tester.** Each person who enters the code gets their own
  synthetic records and their own simulated clock. One tester pressing
  "Restart the day" cannot affect another.
- **Copies clean themselves up.** A copy is deleted after 7 days without use
  (`CARELENS_SANDBOX_IDLE_DAYS`), and at most 200 exist at once
  (`CARELENS_SANDBOX_MAX`).
- **It refuses to start unsafely.** With sandboxes on, the server will not boot
  unless `CARELENS_SECRET_KEY` is a private value of 24+ characters and
  `CARELENS_ACCESS_CODE` is set.

HTTPS also makes the microphone work on phones, which plain `http://` on a
local network address does not.

## Deploy: Supabase for the database, Render for the app

Supabase is hosted PostgreSQL, so the app needs no code change to use it, only
its connection string. Supabase stores the data; it cannot run the Python
server, so the app itself still needs a host. `render.yaml` in the repo root
describes that one web service (built from `services/api/Dockerfile`).

### 1. Create the Supabase database

1. Create a project at supabase.com and set a database password. Letters and
   digits only keeps the next step simple; any other character must be
   percent-encoded when it goes into a URL.
2. In the project, press **Connect** and copy the **Session pooler** string.
   It looks like
   `postgresql://postgres.<project-ref>:<password>@<region>.pooler.supabase.com:5432/postgres`.
   Put your password in place of the placeholder.
   - Use the Session pooler, not "Direct connection": the direct address is
     IPv6-only unless you pay for the IPv4 add-on, and Render cannot reach it.
   - The Transaction pooler (port 6543) also works, because the app turns
     prepared statements off, but the Session pooler is the better fit for one
     long-running server.
3. In the project's API settings, turn the **Data API** off. CareLens talks to
   the database directly and never uses it. CareLens keeps its tables out of
   the `public` schema in hosted mode (`carelens_demo` for the sandbox list,
   one `sb_...` schema per tester), so nothing is published either way; this
   is a second lock.

Treat the connection string like a password. Do not commit it or paste it into
chat tools.

### 2. Deploy the app on Render

1. **Put the repo on GitHub** (a private repository is fine):
   ```bash
   cd carelens
   git init && git add . && git commit -m "CareLens prototype"
   gh repo create carelens --private --source . --push     # or create the repo on github.com and push
   ```
2. **Create the Blueprint.** In the Render dashboard choose New, then
   Blueprint, and pick the repository. Render reads `render.yaml`.
3. **Fill in the two values Render asks for:**
   - `CARELENS_ACCESS_CODE`: the code you will give testers, at least 6
     characters, something you can say aloud (three random words works).
   - `CARELENS_DATABASE_URL`: the Supabase Session pooler string from step 1.
   The secret key is generated for you.
4. **Apply** and wait for the first build (several minutes).
5. **Open the service URL**, enter the code, and run through
   `demos/demo-script.md` yourself before sharing it. In Supabase's Table
   Editor you should now see a `carelens_demo` schema and one `sb_...` schema
   for your copy.

To change the code later, edit `CARELENS_ACCESS_CODE` in the service's
Environment tab. People already inside keep their copy; to end every session,
also regenerate `CARELENS_SECRET_KEY`.

### Free plan limits to expect

Checked against each provider's documentation on 2026-11-23
(https://render.com/docs/free,
https://supabase.com/docs/guides/platform/free-project-pausing); confirm
before relying on them.

- **Render:** a free web service goes to sleep after 15 minutes without
  traffic and takes up to about a minute to wake. Reminders do not fire while
  it sleeps; the engine catches up on wake. For a scheduled usability session,
  open the link a minute early or use a paid instance.
- **Supabase:** a free project is paused after about a week of low activity
  and must be resumed by hand from the dashboard. While it is paused the demo
  shows an error. Open the demo at least every few days during a test period,
  or use a paid plan.

## Any other host

The image is a normal container. Anything that runs Docker and offers
a reachable PostgreSQL works (Fly.io, Railway, a VM). Set:

| Variable | Value |
|---|---|
| `CARELENS_ENV` | `demo` |
| `CARELENS_SANDBOXES` | `1` |
| `CARELENS_ACCESS_CODE` | the code you give testers |
| `CARELENS_SECRET_KEY` | a private random string, 24+ characters |
| `CARELENS_DATABASE_URL` | a PostgreSQL URL: Supabase, Neon, the host's own, or any other (`postgres://...` is accepted) |
| `PORT` | set by most hosts; defaults to 8000 |

Run exactly one instance with one worker. Sandboxes and the reminder loop keep
their indexes in the process; a second instance would not see them.

To rehearse hosted mode on your own machine with no PostgreSQL:

```bash
cd services/api
CARELENS_SANDBOXES=1 CARELENS_ACCESS_CODE=try-it-here \
CARELENS_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(24))')" \
../../.venv/bin/python -m uvicorn app.main:app --port 8000
```

Each tester's copy is then a file under `.sandboxes/`.

## What to send testers

> Here is an early prototype of CareLens, a voice-first reminder assistant for
> older adults. Everything in it is made up; please don't enter real medicines,
> bills or personal details, and don't rely on anything it says.
>
> Link: `https://<your-service>.onrender.com`  Access code: `<code>`
>
> It works best in Chrome. On a phone you can add it to your home screen from
> the browser's share or menu button. The first load can take a minute.
>
> Try: press "Restart the day at 7:55 AM", then "+10 min", and follow the
> suggestions under the list. Then ask it something it shouldn't answer, such
> as "Can I take two today?". Tell me what confused you and what you expected
> to happen.

On a phone the demo controls are below the phone screen; scroll down.

## What has and hasn't been verified

Verified on 2026-11-23: the full test suite (193 tests) on SQLite and on
PostgreSQL 16 with sandboxes as schemas; a browser session with two testers
using a host-style `postgres://` URL, confirming separate data and clocks; a
server restart keeping a tester's copy and clock; refusal to start with a weak
configuration.

Not verified: a connection to a real Supabase project (tested against plain
PostgreSQL 16 with the same driver settings), the Docker image build (the
build environment could not reach Docker Hub), and an actual Render
deployment. Expect to fix small things on the first deploy.

## Before this is more than a demo

Hosted mode makes a synthetic demo safe to share. It does not make the product
ready for real users. Still required: real authentication, encryption at rest,
a retention policy, pharmacist review of the safety rules, a privacy review,
and consent and IRB determination for sessions with older adults.
