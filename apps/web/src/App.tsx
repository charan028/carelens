import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, hasSandbox, onSandboxLost, onSignInLost, setSimulatedOffline, type Persona } from "./api/client";
import { AccessGate } from "./components/AccessGate";
import { DemoControls } from "./components/parts";
import { CaregiverDashboard, VerifierQueue } from "./pages/OtherPersonas";
import { SeniorApp } from "./pages/SeniorApp";
import {
  EventLog,
  OutputRouter,
  PhoneAdapter,
  SimulatedGlassesAdapter,
  unlockSpeechOnFirstTap,
  type WearableEvent,
} from "./wearables/WearableAdapter";

const ROLE_LABEL: Record<Persona["role"], string> = {
  senior: "her phone",
  caregiver: "caregiver",
  verifier: "pharmacist",
};

export function App() {
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [tokens, setTokens] = useState<Record<string, string>>({});
  const [active, setActive] = useState("");
  const [bootError, setBootError] = useState("");

  const [offline, setOffline] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [clock, setClock] = useState({ label: "", simulated: false });
  const offsetMs = useRef(0);
  const nowMs = useCallback(() => Date.now() + offsetMs.current, []);

  const [speechOn, setSpeechOn] = useState(true);
  const [quiet, setQuiet] = useState(false);
  const speechRef = useRef({ enabled: true, rate: 0.92 });
  speechRef.current.enabled = speechOn;

  const [events, setEvents] = useState<WearableEvent[]>([]);
  const [, setDeviceVersion] = useState(0);
  const devices = useMemo(() => {
    const log = new EventLog();
    const phone = new PhoneAdapter(log, () => speechRef.current);
    const glasses = new SimulatedGlassesAdapter(log, () => speechRef.current);
    return { log, phone, glasses, router: new OutputRouter(phone, glasses, log) };
  }, []);

  useEffect(() => {
    devices.glasses.bind(() => setDeviceVersion((version) => version + 1));
    const unsubscribe = devices.log.subscribe((event) => setEvents((list) => [event, ...list].slice(0, 8)));
    const relock = unlockSpeechOnFirstTap();
    return () => {
      unsubscribe();
      relock();
    };
  }, [devices]);

  // "gate" = hosted demo waiting for the access code.
  const [gate, setGate] = useState(false);

  const signingIn = useRef(false);
  const signIn = useCallback(async () => {
    if (signingIn.current) return;
    signingIn.current = true;
    try {
      const config = await api<{ sandboxes: boolean }>("/v1/auth/config");
      if (config.sandboxes && !hasSandbox()) {
        setPersonas([]);
        setGate(true);
        return;
      }
      const { users } = await api<{ users: Persona[] }>("/v1/auth/demo-users");
      const signedIn: Record<string, string> = {};
      for (const user of users) {
        const login = await api<{ token: string }>("/v1/auth/demo-login", { body: { user_id: user.id } });
        signedIn[user.id] = login.token;
      }
      setGate(false);
      setBootError("");
      setTokens(signedIn);
      setPersonas(users);
      setActive(users.find((user) => user.role === "senior")?.id ?? users[0]?.id ?? "");
    } catch (problem) {
      // A lost sandbox (401) is handled by the access screen; anything else is an outage.
      if (!(problem instanceof ApiError && problem.status === 401)) {
        setBootError("CareLens can't reach its server. Check that it is running, then reload this page.");
      }
    } finally {
      signingIn.current = false;
    }
  }, []);

  useEffect(() => {
    onSandboxLost(() => {
      setPersonas([]);
      setGate(true);
    });
    onSignInLost(() => void signIn());
    void signIn();
  }, [signIn]);

  const onServerTime = useCallback((nowIso: string, simulated: boolean) => {
    offsetMs.current = new Date(nowIso).getTime() - Date.now();
    const label = new Date(nowIso).toLocaleString("en-US", {
      weekday: "short", hour: "numeric", minute: "2-digit", timeZone: "America/Chicago",
    });
    setClock((previous) => (previous.label === label && previous.simulated === simulated ? previous : { label, simulated }));
  }, []);

  const changeOffline = (value: boolean) => {
    setSimulatedOffline(value);
    setOffline(value);
  };

  const person = personas.find((candidate) => candidate.id === active);
  const demo = (
    <DemoControls
      clockLabel={clock.label}
      simulated={clock.simulated}
      offline={offline}
      onOffline={changeOffline}
      onClock={(nowIso, simulated) => {
        onServerTime(nowIso, simulated);
        setRefreshKey((key) => key + 1);
      }}
      onReset={() => {
        window.localStorage.removeItem("carelens.today");
        window.localStorage.removeItem("carelens.reportQueue");
        window.location.reload();
      }}
    />
  );

  return (
    <div className="app">
      <header className="masthead">
        <p className="wordmark">CareLens</p>
        <nav className="personas" aria-label="Choose who you are in the demo">
          {personas.map((candidate) => (
            <button
              key={candidate.id}
              className={candidate.id === active ? "is-active" : ""}
              aria-current={candidate.id === active ? "true" : undefined}
              onClick={() => setActive(candidate.id)}
            >
              {candidate.display_name.replace(" (synthetic)", "").split(",")[0]}
              <span>{ROLE_LABEL[candidate.role]}</span>
            </button>
          ))}
        </nav>
        <p className="watermark">Prototype with synthetic data. No real people, prescriptions or bills.</p>
      </header>

      {bootError && <p className="notice notice-problem boot">{bootError}</p>}
      {gate && <AccessGate onEntered={() => void signIn()} />}

      {person?.role === "senior" && (
        <SeniorApp
          key={person.id}
          token={tokens[person.id]}
          devices={devices}
          events={events}
          offline={offline}
          nowMs={nowMs}
          refreshKey={refreshKey}
          onServerTime={onServerTime}
          speech={{ enabled: speechOn, quiet, setEnabled: setSpeechOn, setQuiet }}
          side={demo}
        />
      )}
      {person && person.role !== "senior" && (
        <div className="stage stage-wide">
          {person.role === "caregiver" ? (
            <CaregiverDashboard key={person.id} token={tokens[person.id]} refreshKey={refreshKey} />
          ) : (
            <VerifierQueue token={tokens[person.id]} refreshKey={refreshKey} onChanged={() => setRefreshKey((key) => key + 1)} />
          )}
          <aside className="side">{demo}</aside>
        </div>
      )}
    </div>
  );
}
