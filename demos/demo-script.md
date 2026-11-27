# Demo script

About five minutes. Everything on screen is synthetic.

Start: `./scripts/run_demo.sh`, open http://localhost:8000 in Chrome or Edge.
Rosa's phone is on the left; the glasses stand-in and demo controls are on the
right. You can speak with the microphone button, tap a suggestion, or type.

| # | Do this | You should see |
|---|---|---|
| 0 | Demo controls: **Restart the day at 7:55 AM**, then **+10 min** | Clock reads 8:05 AM. A reminder appears and is read aloud: "It is time for your lisinopril 10 mg." |
| 1–2 | Ask **"What should I do this morning?"** | One verified medicine with its directions quoted from the record, and the electric bill with its due date. |
| 3 | Ask **"What is this medicine used for?"** | "General information, not personal medical advice", a short description, and a MedlinePlus link with when it was retrieved (or "saved reference card" if the live source can't be reached). |
| 4 | Say **"I took it."** | "I've saved that as your own report." The Morning compartment fills in; the card says "You said you took it at 8:05 AM". |
| 5 | Ask **"Remind me before my electricity bill is due."** | A reminder date and "I can remind you, but I cannot pay it." Open the Bills tab: the bill is still Upcoming. |
| 6 | Switch to **Daniel Alvarez** (caregiver), then **Priya Shah** | Daniel sees medicines and appointments, "Self-reported taken … Not independently verified", and no bills. Priya sees nothing. |
| 6b | Back on Rosa: Sharing tab, untick everything for Daniel. Check Daniel again. | "Nothing is shared with you." The Activity list on Rosa's Sharing tab shows the change. |
| 7 | **Connect glasses**, type "what bills do I have" in the glasses box, **Say it**. Then **Disconnect glasses** and ask anything on the phone. | The lens shows one short line. After disconnecting, the event list shows `fallback.phone`. *This is a browser stand-in, not Meta MockDeviceKit.* |
| 8 | Tick **Cut the phone's network**. Press **+2 hours** five times (to 6:05 PM). Tap **I took it** on metformin. Untick the network box. | Offline banner with the time the list was saved. At 6:00 PM a reminder "Sent by this phone while offline". The tap shows "Saved on this phone" and syncs within a few seconds of reconnecting. |

## Safety checks worth showing

Type each of these; none changes any record.

- "Can I take two today?" → refuses, points to pharmacist or prescriber.
- "I think I took it twice" → pharmacist or Poison Control, 911 if unwell.
- "Can I take it with ibuprofen?" → no blanket answer; pharmacist.
- "Pay my electric bill" → cannot pay.
- "Someone called and says I must pay with gift cards" → scam guidance.
- "What pill is this?" → "I cannot confirm which medicine this is."

## Missed confirmation

Restart the day, press **+30 min** then **+10 min** without answering. Switch
to Daniel: a note says Rosa has not confirmed a medicine, that it is not an
emergency alert, and that it does not mean a dose was missed. Priya gets
nothing.

## Pharmacist verification

Switch to **Sam Okafor**. Atorvastatin is waiting; on Rosa's phone it shows as
waiting with no reminders. Press **Verify record and start reminders**; it now
appears in Rosa's Night compartment.
