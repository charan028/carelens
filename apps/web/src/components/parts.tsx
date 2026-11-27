import { useEffect, useState } from "react";
import { api, type Dose, type Source } from "../api/client";

export const AWAITING = ["scheduled", "reminded", "overdue"];

export function clockTime(iso: string, timeZone: string): string {
  return new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone });
}

/** How a dose state should read at a glance: tone drives color and shape. */
export function doseTone(dose: Dose): "done" | "due" | "open" | "later" | "skipped" {
  if (dose.status === "self_reported_taken") return "done";
  if (dose.status === "skipped") return "skipped";
  if (dose.status === "reminded" || dose.status === "overdue") return "due";
  if (dose.status === "unknown") return "open";
  return "later";
}

export function StatusChip({ tone, children }: { tone: string; children: React.ReactNode }) {
  return <span className={`chip chip-${tone}`}>{children}</span>;
}

// --- the day strip: a pill organizer for today -------------------------------

const PERIODS: { id: Dose["period"]; label: string }[] = [
  { id: "morning", label: "Morning" },
  { id: "midday", label: "Midday" },
  { id: "evening", label: "Evening" },
  { id: "night", label: "Night" },
];

export function periodOfHour(hour: number): Dose["period"] {
  if (hour < 11) return "morning";
  if (hour < 15) return "midday";
  if (hour < 20) return "evening";
  return "night";
}

export function DayStrip({ doses, current, onJump }: { doses: Dose[]; current: Dose["period"]; onJump: (period: Dose["period"]) => void }) {
  return (
    <ol className="daystrip" aria-label="Today's medicines by time of day">
      {PERIODS.map((period) => {
        const mine = doses.filter((dose) => dose.period === period.id);
        const summary = mine.length
          ? mine.map((dose) => `${dose.name}, ${dose.local_time}: ${dose.status_label}`).join("; ")
          : "nothing scheduled";
        return (
          <li key={period.id}>
            <button
              type="button"
              className={`compartment ${period.id === current ? "is-now" : ""}`}
              disabled={mine.length === 0}
              onClick={() => onJump(period.id)}
              aria-label={`${period.label}: ${summary}${mine.length ? ". Show these medicines" : ""}`}
            >
              <span className="compartment-label">{period.label}</span>
              <span className="compartment-pills" aria-hidden="true">
                {mine.length === 0 && <span className="pill pill-none" />}
                {mine.map((dose) => (
                  <span key={dose.dose_event_id} className={`pill pill-${doseTone(dose)}`} />
                ))}
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

// --- demo drawer -------------------------------------------------------------

/** Who you are viewing as, and the demo clock. Closed by default. */
const DRAWER_KEY = "carelens.demobar";

export function DemoBar({ summary, children }: { summary: React.ReactNode; children: React.ReactNode }) {
  // Stays open across "Restart the day", which reloads the page.
  const [open, setOpen] = useState(() => {
    try {
      return window.sessionStorage.getItem(DRAWER_KEY) === "open";
    } catch {
      return false;
    }
  });
  useEffect(() => {
    try {
      window.sessionStorage.setItem(DRAWER_KEY, open ? "open" : "closed");
    } catch {
      /* private mode: the drawer just starts closed after a reload */
    }
  }, [open]);
  return (
    <section className="demobar" aria-label="Demo: viewer and clock">
      <button className="demobar-summary" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        <span>{summary}</span>
        <span className="hint">{open ? "Hide ▴" : "Switch person or move the clock ▾"}</span>
      </button>
      {open && <div className="demobar-body">{children}</div>}
    </section>
  );
}

// --- sources -------------------------------------------------------------------

export function SourceList({ sources, timeZone }: { sources: Source[]; timeZone: string }) {
  if (sources.length === 0) return null;
  return (
    <ul className="sources">
      {sources.map((source) => (
        <li key={source.url}>
          <a href={source.url} target="_blank" rel="noreferrer noopener">
            {source.source}: {source.title}
          </a>
          <span className="source-meta">
            {"fetched_at" in source && source.fetched_at
              ? source.origin === "bundled"
                ? "Saved reference card, not a live lookup"
                : `Retrieved ${clockTime(source.fetched_at, timeZone)}${source.stale ? ", may be out of date" : ""}`
              : "Official guidance"}
          </span>
        </li>
      ))}
    </ul>
  );
}

// --- demo controls ---------------------------------------------------------------

interface DemoProps {
  clockLabel: string;
  simulated: boolean;
  offline: boolean;
  onOffline: (value: boolean) => void;
  onClock: (nowIso: string, simulated: boolean) => void;
  onReset: () => void;
}

export function DemoControls({ clockLabel, simulated, offline, onOffline, onClock, onReset }: DemoProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const run = async (path: string, body: object, wasReset = false) => {
    setBusy(true);
    setError("");
    try {
      const result = await api<{ now: string; simulated?: boolean }>(path, { body, bypassOffline: true });
      if (wasReset) onReset();
      onClock(result.now, result.simulated ?? true);
    } catch {
      setError("The server did not answer. Is the API running?");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="demo-controls">
      <h3>Clock</h3>
      <p className="clock">
        {clockLabel || "…"} <span className="panel-note">{simulated ? "simulated clock" : "real time"}</span>
      </p>
      <div className="row">
        <button className="button button-primary" disabled={busy} onClick={() => run("/v1/dev/reset", { set_local_time: "07:55" }, true)}>
          Restart the day at 7:55 AM
        </button>
      </div>
      <div className="row">
        {[10, 30, 120].map((minutes) => (
          <button key={minutes} className="button" disabled={busy} onClick={() => run("/v1/dev/clock", { advance_minutes: minutes })}>
            +{minutes >= 60 ? `${minutes / 60} hours` : `${minutes} min`}
          </button>
        ))}
      </div>
      <label className="switch">
        <input type="checkbox" checked={offline} onChange={(event) => onOffline(event.target.checked)} />
        <span>Cut the phone&rsquo;s network</span>
      </label>
      {error && <p className="error">{error}</p>}
      <p className="panel-note">
        Restarting wipes the database and reloads the synthetic records. Moving the clock runs the
        reminder engine for every step, as it would run live.
      </p>
    </div>
  );
}
