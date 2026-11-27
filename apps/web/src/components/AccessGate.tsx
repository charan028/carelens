import { useState, type FormEvent } from "react";
import { api, ApiError, setSandboxToken } from "../api/client";

/** First screen of the hosted demo: trade the access code for a private copy. */
export function AccessGate({ onEntered }: { onEntered: () => void }) {
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await api<{ sandbox_token: string }>("/v1/sandbox", { body: { access_code: code } });
      // Start clean: nothing saved on this device from an earlier copy.
      window.localStorage.removeItem("carelens.today");
      window.localStorage.removeItem("carelens.reportQueue");
      setSandboxToken(result.sandbox_token);
      onEntered();
    } catch (problem) {
      setError(
        problem instanceof ApiError
          ? problem.message
          : "The demo server didn't answer. Check your connection and try again.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="gate">
      <h1>Try the CareLens prototype</h1>
      <p>
        CareLens is a voice-first reminder assistant for older adults. This is an early test
        version, and everything in it is made up.
      </p>
      <form onSubmit={submit}>
        <label>
          Access code
          <input
            value={code}
            onChange={(event) => setCode(event.target.value)}
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            maxLength={200}
            required
            autoFocus
          />
        </label>
        {error && <p className="notice notice-problem" role="alert">{error}</p>}
        <button className="button button-primary" disabled={busy || !code.trim()}>
          {busy ? "Setting up your copy…" : "Start the demo"}
        </button>
      </form>
      <ul>
        <li>You get your own copy. Nothing you do affects other testers.</li>
        <li>Don&rsquo;t enter real medicines, bills or personal details.</li>
        <li>It is not medical advice and not an emergency service.</li>
        <li>Your copy is deleted after a week without use.</li>
      </ul>
    </main>
  );
}
