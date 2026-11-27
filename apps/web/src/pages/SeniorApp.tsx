import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import {
  api,
  ApiError,
  OfflineError,
  type AssistantReply,
  type Bill,
  type Consents,
  type Dose,
  type Notice,
  type Today,
} from "../api/client";
import { applyQueue, dueLocalReminders, flushQueue, OfflineStore } from "../api/offline";
import { AWAITING, clockTime, DayStrip, doseTone, periodOfHour, SourceList, StatusChip } from "../components/parts";
import { stopListening, stopSpeaking, voiceInputAvailable, type PhoneAdapter } from "../wearables/WearableAdapter";

const SUGGESTIONS = [
  "What should I do this morning?",
  "What is this medicine used for?",
  "I took it.",
  "Remind me before my electricity bill is due.",
];

const OFFLINE_REPLY =
  "I can't reach CareLens right now, so I can't look that up or save anything by voice. " +
  "Your reminders still work on this phone, and you can use the buttons on your list. " +
  "For questions about a medicine, call your pharmacist.";

interface Props {
  token: string;
  phone: PhoneAdapter;
  offline: boolean;
  nowMs: () => number;
  refreshKey: number;
  onServerTime: (nowIso: string, simulated: boolean) => void;
  speech: { enabled: boolean; setEnabled: (v: boolean) => void };
}

type Tab = "today" | "bills" | "sharing";
type Exchange = { question: string; reply: AssistantReply };

const store = new OfflineStore(window.localStorage);

export function SeniorApp({ token, phone, offline, nowMs, refreshKey, onServerTime, speech }: Props) {
  const [today, setToday] = useState<Today | null>(null);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("today");
  const [exchange, setExchange] = useState<Exchange | null>(null);
  const [banner, setBanner] = useState<{ title: string; body: string; local: boolean } | null>(null);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [listening, setListening] = useState(false);
  const [problem, setProblem] = useState("");
  const [queued, setQueued] = useState(store.queue().length);

  const seenNotes = useRef<Set<string> | null>(null);
  const announced = useRef(new Set<string>());
  // Doses this phone already announced by itself while offline.
  const wasLocal = useRef(new Set<string>());
  const bodyRef = useRef<HTMLDivElement>(null);
  const context = useRef<{ focus_dose_event_id?: string; last_reply?: string }>({});
  const timeZone = today?.user.timezone ?? "America/Chicago";

  const announce = useCallback(
    (title: string, body: string, local: boolean) => {
      setBanner({ title, body, local });
      phone.present({ speech: body, display: title });
    },
    [phone],
  );

  const load = useCallback(async () => {
    try {
      const flushed = await flushQueue(store, (report) =>
        api(`/v1/dose-events/${report.doseEventId}/self-report`, {
          token,
          body: { status: report.status },
          idempotencyKey: report.key,
        }).then(() => undefined),
      );
      setQueued(flushed.left);
      const fresh = await api<Today>("/v1/me/today", { token });
      store.saveToday(fresh);
      setToday(fresh);
      setSavedAt(null);
      onServerTime(fresh.now, fresh.clock_simulated);

      const { notifications } = await api<{ notifications: Notice[] }>("/v1/notifications", { token });
      const first = seenNotes.current === null;
      const seen = seenNotes.current ?? new Set<string>();
      const fresh_notes = notifications.filter((note) => !seen.has(note.id)).reverse();
      for (const note of fresh_notes) {
        seen.add(note.id);
        // Don't repeat a reminder this phone already gave by itself offline.
        const alreadyLocal = note.kind === "dose_reminder" && wasLocal.current.has(note.resource_id);
        if (note.kind === "dose_reminder") announced.current.add(note.resource_id);
        if (!first && !alreadyLocal) announce(note.title, note.body, false);
      }
      seenNotes.current = seen;
    } catch (error) {
      if (error instanceof OfflineError) {
        const saved = store.loadToday();
        if (saved) {
          setToday(applyQueue(saved.today, store.queue()));
          setSavedAt(saved.savedAt);
        }
        return;
      }
      setProblem(error instanceof ApiError ? error.message : "Something went wrong loading your list.");
    }
  }, [token, announce, onServerTime]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 4000);
    return () => window.clearInterval(timer);
  }, [load, refreshKey, offline]);

  // Phone-local reminders: no server involved. Runs only while offline.
  useEffect(() => {
    if (!offline) return;
    const timer = window.setInterval(() => {
      const saved = store.loadToday();
      if (!saved) return;
      const plan = applyQueue(saved.today, store.queue());
      for (const dose of dueLocalReminders(plan, nowMs(), announced.current)) {
        announced.current.add(dose.dose_event_id);
        wasLocal.current.add(dose.dose_event_id);
        announce(
          `${dose.local_time} medicine`,
          `It is time for your ${dose.name} ${dose.strength}. Tap a button on your list when you have taken it.`,
          true,
        );
      }
    }, 1500);
    return () => window.clearInterval(timer);
  }, [offline, nowMs, announce]);

  const ask = async (question: string) => {
    const text = question.trim();
    if (!text || busy) return;
    setBusy(true);
    setProblem("");
    try {
      let reply: AssistantReply;
      try {
        reply = await api<AssistantReply>("/v1/assistant/message", {
          token,
          body: { text, channel: "phone", context: context.current },
        });
      } catch (error) {
        if (!(error instanceof OfflineError)) throw error;
        reply = {
          text: OFFLINE_REPLY, display: "Offline. Reminders still work on the phone.", intent: "offline",
          policy: { action: "allow", category: "offline" }, sources: [], focus: null, options: [],
          tool_calls: [], changed: false,
        };
      }
      if (reply.focus) context.current.focus_dose_event_id = reply.focus.dose_event_id;
      context.current.last_reply = reply.text;
      setExchange({ question: text, reply });
      setTab("today");
      bodyRef.current?.scrollTo({ top: 0 });
      phone.present({ speech: reply.text, display: reply.display });
      if (reply.changed) {
        setBanner(null);
        await load();
      }
    } catch (error) {
      setProblem(error instanceof ApiError ? error.message : "I couldn't get an answer. Try again.");
    } finally {
      setBusy(false);
    }
  };

  const listen = async () => {
    if (listening) {
      stopListening(); // second tap: "I'm done talking"
      return;
    }
    setProblem("");
    setListening(true);
    setTyped("");
    try {
      // Words appear in the box as they are heard, so the person can see it is working.
      const heard = await phone.listen((text) => setTyped(text));
      setListening(false);
      setTyped("");
      await ask(heard);
    } catch (error) {
      // Whatever was heard stays in the box, so it can be fixed and sent by hand.
      setListening(false);
      setProblem((error as Error).message);
    }
  };

  const report = async (dose: Dose, status: "taken" | "skipped" | "unsure") => {
    setProblem("");
    try {
      await api(`/v1/dose-events/${dose.dose_event_id}/self-report`, {
        token,
        body: { status },
        idempotencyKey: `tap-${dose.dose_event_id}-${status}-${Date.now().toString(36)}`,
      });
    } catch (error) {
      if (!(error instanceof OfflineError)) {
        setProblem(error instanceof ApiError ? error.message : "That wasn't saved. Try again.");
        return;
      }
      store.enqueue({ doseEventId: dose.dose_event_id, status, at: new Date(nowMs()).toISOString() });
      setQueued(store.queue().length);
    }
    setBanner(null);
    await load();
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const text = typed;
    setTyped("");
    void ask(text);
  };

  const hour = Number(
    new Intl.DateTimeFormat("en-US", { hour: "numeric", hour12: false, timeZone }).format(nowMs()),
  ) % 24;

  return (
    <div className="stage">
      <div className="phone" aria-label="Rosa's phone">
        <header className="phone-head">
          <div>
            <p className="phone-date">{today?.date_label ?? "Loading your day"}</p>
            <p className="phone-sub">
              {today ? clockTime(new Date(nowMs()).toISOString(), timeZone) : ""}
              {today?.synthetic_notice ? " · synthetic demo data" : ""}
            </p>
          </div>
          <label className="switch switch-small">
            <input type="checkbox" checked={speech.enabled} onChange={(event) => { speech.setEnabled(event.target.checked); if (!event.target.checked) stopSpeaking(); }} />
            <span>Read aloud</span>
          </label>
        </header>

        {savedAt && (
          <p className="notice notice-offline" role="status">
            You&rsquo;re offline. This is your list as saved at {clockTime(savedAt, timeZone)}. Reminders still
            work on this phone.{queued > 0 ? ` ${queued} answer${queued === 1 ? "" : "s"} will sync when you're back online.` : ""}
          </p>
        )}
        {banner && (
          <div className="notice notice-reminder" role="alert">
            <div>
              <strong>{banner.title}</strong>
              <p>{banner.body}</p>
              {banner.local && <p className="notice-meta">Sent by this phone while offline.</p>}
            </div>
            <button className="button button-quiet" onClick={() => setBanner(null)}>Dismiss</button>
          </div>
        )}
        {problem && <p className="notice notice-problem" role="alert">{problem}</p>}

        <div className="phone-body" ref={bodyRef}>
          {exchange && tab === "today" && (
            <section className={`answer ${exchange.reply.intent === "safety_refusal" ? "answer-refusal" : ""}`} aria-live="polite">
              <p className="answer-question">You asked: “{exchange.question}”</p>
              <p className="answer-text">{exchange.reply.text}</p>
              {exchange.reply.options.length > 0 && (
                <div className="row">
                  {exchange.reply.options.map((option) => (
                    <button key={option} className="button" onClick={() => void ask(`${exchange.question} ${option}`)}>
                      {option}
                    </button>
                  ))}
                </div>
              )}
              <SourceList sources={exchange.reply.sources} timeZone={timeZone} />
            </section>
          )}
          {today && tab === "today" && (
            <TodayScreen today={today} hour={hour} timeZone={timeZone} onReport={report} onBills={() => setTab("bills")} />
          )}
          {today && tab === "bills" && <BillsScreen token={token} offline={offline} onChanged={load} refreshKey={refreshKey} />}
          {tab === "sharing" && <SharingScreen token={token} offline={offline} />}
        </div>

        <footer className="talk">
          {tab === "today" && (
            <div className="suggestions">
              {SUGGESTIONS.map((suggestion) => (
                <button key={suggestion} className="suggestion" disabled={busy} onClick={() => void ask(suggestion)}>
                  {suggestion}
                </button>
              ))}
            </div>
          )}
          <form className="talk-row" onSubmit={onSubmit}>
            <button
              type="button"
              className={`mic ${listening ? "is-listening" : ""}`}
              onClick={() => void listen()}
              // Stays tappable without voice support: the tap explains why, which a tooltip can't on a phone.
              disabled={busy}
              aria-label={listening ? "Done talking" : voiceInputAvailable() ? "Talk to CareLens" : "Voice input isn't available in this browser"}
            >
              <svg viewBox="0 0 24 24" width="28" height="28" aria-hidden="true">
                <rect x="9" y="3" width="6" height="11" rx="3" fill="currentColor" />
                <path d="M6 11a6 6 0 0 0 12 0M12 17v4" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" />
              </svg>
            </button>
            <label className="visually-hidden" htmlFor="ask">Type a question</label>
            <input id="ask" value={typed} onChange={(event) => setTyped(event.target.value)} placeholder={listening ? "Listening… tap the microphone when you're done" : "Or type here"} maxLength={400} />
            <button className="button button-primary" disabled={busy || !typed.trim()}>Ask</button>
          </form>
          <nav className="tabs" aria-label="Screens">
            {(["today", "bills", "sharing"] as Tab[]).map((name) => (
              <button key={name} className={tab === name ? "is-active" : ""} onClick={() => setTab(name)} aria-current={tab === name ? "page" : undefined}>
                {name === "today" ? "Today" : name === "bills" ? "Bills" : "Sharing"}
              </button>
            ))}
          </nav>
        </footer>
      </div>
    </div>
  );
}

// --- Today -------------------------------------------------------------------

interface TodayProps {
  today: Today;
  hour: number;
  timeZone: string;
  onReport: (dose: Dose, status: "taken" | "skipped" | "unsure") => void;
  onBills: () => void;
}

function TodayScreen({ today, hour, timeZone, onReport, onBills }: TodayProps) {
  const openBills = today.bills.filter((bill) => ["upcoming", "due", "overdue"].includes(bill.status));
  const jump = (period: Dose["period"]) =>
    document.querySelector(`[data-period="${period}"]`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  return (
    <>
      <DayStrip doses={today.medications} current={periodOfHour(hour)} onJump={jump} />

      <section aria-labelledby="meds-title">
        <h2 id="meds-title">Medicines</h2>
        {today.medications.length === 0 && <p className="empty">No verified medicines are scheduled today.</p>}
        {today.medications.map((dose) => {
          const awaiting = AWAITING.includes(dose.status) || dose.status === "unknown";
          return (
            <article key={dose.dose_event_id} className={`task task-${doseTone(dose)}`} data-period={dose.period}>
              <p className="task-time">{dose.local_time}</p>
              <div className="task-main">
                <h3>
                  {dose.name} <span className="task-strength">{dose.strength}</span>
                </h3>
                <p className="task-directions">“{dose.directions}”</p>
                <p className="task-meta">From your pharmacist-verified record</p>
                <StatusChip tone={doseTone(dose)}>
                  {dose.status === "self_reported_taken" && dose.response_at
                    ? `You said you took it at ${clockTime(dose.response_at, timeZone)}`
                    : dose.status === "skipped"
                      ? "You said you skipped it"
                      : dose.status_label}
                </StatusChip>
                {dose.pending_sync && <p className="task-meta">Saved on this phone. It will sync when you&rsquo;re back online.</p>}
                {awaiting && (
                  <div className="row">
                    <button className="button button-primary" onClick={() => onReport(dose, "taken")}>I took it</button>
                    <button className="button" onClick={() => onReport(dose, "skipped")}>I skipped it</button>
                    <button className="button" onClick={() => onReport(dose, "unsure")}>Not sure</button>
                  </div>
                )}
              </div>
            </article>
          );
        })}
        {today.awaiting_verification.map((item) => (
          <p key={item.prescription_id} className="pending">
            {item.name} {item.strength} is waiting for a pharmacist to verify it. No reminders are set for it yet.
          </p>
        ))}
      </section>

      <section aria-labelledby="bills-title">
        <h2 id="bills-title">Bills</h2>
        {openBills.length === 0 && <p className="empty">No bills are due in the next two weeks.</p>}
        {openBills.map((bill) => (
          <article key={bill.id} className="line">
            <div>
              <h3>{bill.payee_label}</h3>
              <p className="task-meta">{bill.amount ? `${bill.amount}, ` : ""}{dueText(bill)}</p>
            </div>
            <button className="button button-quiet" onClick={onBills}>Open</button>
          </article>
        ))}
      </section>
    </>
  );
}

function dueText(bill: Bill): string {
  if (bill.days_until_due === 0) return "due today";
  if (bill.days_until_due < 0) return `was due ${bill.due_label}`;
  return `due ${bill.due_label}`;
}

// --- Bills -------------------------------------------------------------------

function BillsScreen({ token, offline, onChanged, refreshKey }: { token: string; offline: boolean; onChanged: () => void; refreshKey: number }) {
  const [bills, setBills] = useState<Bill[] | null>(null);
  const [error, setError] = useState("");
  const [form, setForm] = useState({ payee: "", due: "", amount: "", recurrence: "monthly" });

  const load = useCallback(async () => {
    try {
      setBills((await api<{ bills: Bill[] }>("/v1/bills", { token })).bills);
      setError("");
    } catch (problem) {
      setError(problem instanceof OfflineError ? "Bills can't be changed while you're offline." : (problem as Error).message);
    }
  }, [token]);

  useEffect(() => void load(), [load, offline, refreshKey]);

  const paid = async (bill: Bill) => {
    try {
      await api(`/v1/bills/${bill.id}/status`, { token, body: { status: "self_reported_paid", source: "self_report" } });
      await load();
      onChanged();
    } catch (problem) {
      setError(problem instanceof OfflineError ? "Bills can't be changed while you're offline." : (problem as Error).message);
    }
  };

  const add = async (event: FormEvent) => {
    event.preventDefault();
    const dollars = form.amount.trim() ? Math.round(Number(form.amount) * 100) : null;
    if (dollars !== null && (!Number.isFinite(dollars) || dollars < 0)) {
      setError("Enter the amount as a number, like 84.20, or leave it empty.");
      return;
    }
    try {
      await api("/v1/bills", { token, body: { payee_label: form.payee, due_date: form.due, amount_cents: dollars, recurrence: form.recurrence } });
      setForm({ payee: "", due: "", amount: "", recurrence: "monthly" });
      await load();
      onChanged();
    } catch (problem) {
      setError(problem instanceof OfflineError ? "Bills can't be added while you're offline." : (problem as Error).message);
    }
  };

  return (
    <>
      <section aria-labelledby="allbills-title">
        <h2 id="allbills-title">Your bills</h2>
        <p className="task-meta">CareLens reminds you. It never pays a bill and never needs a card or bank password.</p>
        {error && <p className="notice notice-problem">{error}</p>}
        {bills?.length === 0 && <p className="empty">No bills yet. Add one below to get a reminder before it&rsquo;s due.</p>}
        {bills?.map((bill) => {
          const open = ["upcoming", "due", "overdue"].includes(bill.status);
          const tone = open ? (bill.status === "upcoming" ? "later" : "due") : "done";
          return (
            <article key={bill.id} className={`task task-${tone}`}>
              <div className="task-main">
                <h3>{bill.payee_label}</h3>
                <p className="task-meta">{bill.amount ? `${bill.amount}, ` : "Amount not entered, "}{dueText(bill)}{bill.recurrence !== "none" ? `, repeats ${bill.recurrence}` : ""}</p>
                <StatusChip tone={tone}>
                  {bill.status === "self_reported_paid" ? "You said you paid. Not confirmed by the company." : bill.status_label}
                </StatusChip>
                {open && (
                  <div className="row">
                    <button className="button button-primary" onClick={() => void paid(bill)}>I paid this</button>
                  </div>
                )}
              </div>
            </article>
          );
        })}
      </section>
      <section aria-labelledby="addbill-title">
        <h2 id="addbill-title">Add a bill</h2>
        <form className="form" onSubmit={add}>
          <label>Who is it from?<input required maxLength={120} value={form.payee} onChange={(event) => setForm({ ...form, payee: event.target.value })} /></label>
          <label>Due date<input required type="date" value={form.due} onChange={(event) => setForm({ ...form, due: event.target.value })} /></label>
          <label>Amount in dollars (optional)<input inputMode="decimal" value={form.amount} onChange={(event) => setForm({ ...form, amount: event.target.value })} /></label>
          <label>Repeats
            <select value={form.recurrence} onChange={(event) => setForm({ ...form, recurrence: event.target.value })}>
              <option value="none">Does not repeat</option>
              <option value="weekly">Every week</option>
              <option value="monthly">Every month</option>
              <option value="yearly">Every year</option>
            </select>
          </label>
          <button className="button button-primary">Add bill reminder</button>
        </form>
      </section>
    </>
  );
}

// --- Sharing -----------------------------------------------------------------

const SCOPE_LABELS: Record<string, string> = { medications: "Medicines", bills: "Bills" };

function SharingScreen({ token, offline }: { token: string; offline: boolean }) {
  const [consents, setConsents] = useState<Consents | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setConsents(await api<Consents>("/v1/consents", { token }));
      setError("");
    } catch (problem) {
      setError(problem instanceof OfflineError ? "Sharing can't be changed while you're offline." : (problem as Error).message);
    }
  }, [token]);

  useEffect(() => void load(), [load, offline]);

  const change = async (body: object) => {
    try {
      setConsents(await api<Consents>("/v1/consents", { token, body }));
    } catch (problem) {
      setError(problem instanceof OfflineError ? "Sharing can't be changed while you're offline." : (problem as Error).message);
    }
  };

  return (
    <section aria-labelledby="share-title">
      <h2 id="share-title">Who can see your list</h2>
      <p className="task-meta">You decide. Nothing is shared unless you turn it on here, and you can stop at any time.</p>
      {error && <p className="notice notice-problem">{error}</p>}
      {consents?.caregivers.map((person) => {
        const toggle = (scope: string) => {
          const scopes = person.scopes.includes(scope) ? person.scopes.filter((s) => s !== scope) : [...person.scopes, scope];
          void change({ action: scopes.length ? "grant" : "revoke", caregiver_id: person.caregiver_id, scopes, alerts_enabled: person.alerts_enabled && scopes.length > 0 });
        };
        return (
          <article key={person.caregiver_id} className="task task-later">
            <div className="task-main">
              <h3>{person.display_name}</h3>
              <p className="task-meta">{person.scopes.length ? "Can see what you tick below" : "Sees nothing"}</p>
              {Object.entries(SCOPE_LABELS).map(([scope, label]) => (
                <label key={scope} className="switch">
                  <input type="checkbox" checked={person.scopes.includes(scope)} onChange={() => toggle(scope)} />
                  <span>{label}</span>
                </label>
              ))}
              {person.scopes.length > 0 && (
                <button className="button button-quiet" onClick={() => void change({ action: "revoke", caregiver_id: person.caregiver_id, scopes: [] })}>
                  Stop sharing with {person.display_name.split(" ")[0]}
                </button>
              )}
            </div>
          </article>
        );
      })}
    </section>
  );
}
