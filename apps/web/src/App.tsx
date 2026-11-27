import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, onSignInLost, setSimulatedOffline, type Persona } from "./api/client";
import { DemoBar, DemoControls } from "./components/parts";
import { CaregiverDashboard, VerifierQueue } from "./pages/OtherPersonas";
import { SeniorApp } from "./pages/SeniorApp";
import { PhoneAdapter, unlockSpeechOnFirstTap } from "./wearables/WearableAdapter";

const ROLE_LABEL: Record<Persona["role"], string> = {
  senior: "her phone",
  caregiver: "caregiver",
  verifier: "pharmacist",
};

const shortName = (person: Persona) => person.display_name.replace(" (synthetic)", "").split(",")[0];

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
  const speechRef = useRef({ enabled: true, rate: 0.92 });
  speechRef.current.enabled = speechOn;
  const phone = useMemo(() => new PhoneAdapter(() => speechRef.current), []);

  useEffect(() => unlockSpeechOnFirstTap(), []);

  const signingIn = useRef(false);
  const signIn = useCallback(async () => {
    if (signingIn.current) return;
    signingIn.current = true;
    try {
      const { users } = await api<{ users: Persona[] }>("/v1/auth/demo-users");
      const signedIn: Record<string, string> = {};
      for (const user of users) {
        const login = await api<{ token: string }>("/v1/auth/demo-login", { body: { user_id: user.id } });
        signedIn[user.id] = login.token;
      }
      setBootError("");
      setTokens(signedIn);
      setPersonas(users);
      setActive((current) => current || (users.find((user) => user.role === "senior")?.id ?? users[0]?.id ?? ""));
    } catch {
      setBootError("CareLens can't reach its server. Check that it is running, then reload this page.");
    } finally {
      signingIn.current = false;
    }
  }, []);

  useEffect(() => {
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

  return (
    <div className="app">
      <DemoBar
        summary={
          person ? (
            <>Demo · viewing as <strong>{shortName(person)}</strong> ({ROLE_LABEL[person.role]}){clock.label ? ` · ${clock.label}` : ""}</>
          ) : (
            <>CareLens demo</>
          )
        }
      >
        <div>
          <h3>Who you are in the demo</h3>
          <nav className="personas" aria-label="Choose who you are in the demo">
            {personas.map((candidate) => (
              <button
                key={candidate.id}
                className={candidate.id === active ? "is-active" : ""}
                aria-current={candidate.id === active ? "true" : undefined}
                onClick={() => setActive(candidate.id)}
              >
                {shortName(candidate)}
                <span>{ROLE_LABEL[candidate.role]}</span>
              </button>
            ))}
          </nav>
        </div>
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
      </DemoBar>

      {bootError && <p className="notice notice-problem boot">{bootError}</p>}

      {person?.role === "senior" && (
        <SeniorApp
          key={person.id}
          token={tokens[person.id]}
          phone={phone}
          offline={offline}
          nowMs={nowMs}
          refreshKey={refreshKey}
          onServerTime={onServerTime}
          speech={{ enabled: speechOn, setEnabled: setSpeechOn }}
        />
      )}
      {person?.role === "caregiver" && <CaregiverDashboard key={person.id} token={tokens[person.id]} refreshKey={refreshKey} />}
      {person?.role === "verifier" && (
        <VerifierQueue token={tokens[person.id]} refreshKey={refreshKey} onChanged={() => setRefreshKey((key) => key + 1)} />
      )}

      <p className="watermark">Prototype with synthetic data. No real people, prescriptions or bills.</p>
    </div>
  );
}
