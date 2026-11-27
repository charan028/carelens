import { useState, type FormEvent } from "react";
import { api, type Dose, type Source } from "../api/client";
import type { SimulatedGlassesAdapter, WearableEvent } from "../wearables/WearableAdapter";
import { voiceInputAvailable } from "../wearables/WearableAdapter";

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

export function DayStrip({ doses, current }: { doses: Dose[]; current: Dose["period"] }) {
  return (
    <ol className="daystrip" aria-label="Today's medicines by time of day">
      {PERIODS.map((period) => {
        const mine = doses.filter((dose) => dose.period === period.id);
        return (
          <li key={period.id} className={`compartment ${period.id === current ? "is-now" : ""}`}>
            <span className="compartment-label">{period.label}</span>
            <span className="compartment-pills">
              {mine.length === 0 && <span className="pill pill-none" aria-label="Nothing scheduled" />}
              {mine.map((dose) => (
                <span
                  key={dose.dose_event_id}
                  className={`pill pill-${doseTone(dose)}`}
                  title={`${dose.name}, ${dose.local_time}: ${dose.status_label}`}
                >
                  <span className="visually-hidden">
                    {dose.name}, {dose.local_time}: {dose.status_label}
                  </span>
                </span>
              ))}
            </span>
          </li>
        );
      })}
    </ol>
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

// --- glasses stand-in ------------------------------------------------------------

interface GlassesProps {
  glasses: SimulatedGlassesAdapter;
  events: WearableEvent[];
  onSay: (text: string) => void;
  onListen: () => void;
  listening: boolean;
}

export function GlassesPanel({ glasses, events, onSay, onListen, listening }: GlassesProps) {
  const [typed, setTyped] = useState("");
  const connected = glasses.isConnected();
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (typed.trim()) onSay(typed.trim());
    setTyped("");
  };
  return (
    <section className="panel" aria-labelledby="glasses-title">
      <h2 id="glasses-title">Glasses stand-in</h2>
      <p className="panel-note">
        A browser model of a wearable, so the phone fallback can be shown. It is not Meta&rsquo;s
        MockDeviceKit and says nothing about real glasses.
      </p>
      <div className={`lens ${connected ? "is-on" : ""}`} aria-live="polite">
        {connected ? glasses.displayLine() || "Ready" : "Not connected"}
      </div>
      <div className="row">
        <button className="button" onClick={() => (connected ? glasses.disconnect() : glasses.connect())}>
          {connected ? "Disconnect glasses" : "Connect glasses"}
        </button>
        <button
          className="button"
          onClick={onListen}
          disabled={!connected || listening || !voiceInputAvailable()}
          title={voiceInputAvailable() ? "" : "Voice input isn't available in this browser"}
        >
          {listening ? "Listening…" : "Talk through glasses"}
        </button>
      </div>
      <form className="row" onSubmit={submit}>
        <label className="visually-hidden" htmlFor="glasses-say">
          Type what the wearer says
        </label>
        <input
          id="glasses-say"
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
          placeholder="Or type what the wearer says"
          disabled={!connected}
          maxLength={400}
        />
        <button className="button" disabled={!connected || !typed.trim()}>
          Say it
        </button>
      </form>
      <h3>Device events</h3>
      {events.length === 0 ? (
        <p className="panel-note">Nothing yet. Connect the glasses, then ask something.</p>
      ) : (
        <ol className="eventlog">
          {events.map((event, index) => (
            <li key={`${event.at}-${index}`}>
              <code>{event.type}</code> {event.detail}
            </li>
          ))}
        </ol>
      )}
    </section>
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
    <section className="panel" aria-labelledby="demo-title">
      <h2 id="demo-title">Demo controls</h2>
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
    </section>
  );
}
