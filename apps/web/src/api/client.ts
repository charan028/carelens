// Thin API client. One place decides whether we are online, so every screen
// behaves the same way when the network is gone.

export class OfflineError extends Error {
  constructor() {
    super("offline");
  }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

let simulatedOffline = false;

/** Demo switch that makes the phone behave as if it had no network. */
export function setSimulatedOffline(value: boolean): void {
  simulatedOffline = value;
}

export function isOffline(): boolean {
  return simulatedOffline || !navigator.onLine;
}

// --- hosted demo: each tester's private sandbox --------------------------------

const SANDBOX_KEY = "carelens.sandbox";
let sandboxToken: string | null = null;
try {
  sandboxToken = window.localStorage.getItem(SANDBOX_KEY);
} catch {
  /* storage unavailable: the tester re-enters the code after a reload */
}
let sandboxLost: () => void = () => {};

export function hasSandbox(): boolean {
  return sandboxToken !== null;
}

export function setSandboxToken(token: string | null): void {
  sandboxToken = token;
  try {
    if (token) window.localStorage.setItem(SANDBOX_KEY, token);
    else window.localStorage.removeItem(SANDBOX_KEY);
  } catch {
    /* see above */
  }
}

/** Called when the server says the sandbox is gone (expired or unknown). */
export function onSandboxLost(handler: () => void): void {
  sandboxLost = handler;
}

let signInLost: () => void = () => {};

/** Called when the server rejects a sign-in token (for example after a restart). */
export function onSignInLost(handler: () => void): void {
  signInLost = handler;
}

interface Options {
  method?: "GET" | "POST";
  body?: unknown;
  token?: string;
  idempotencyKey?: string;
  /** Demo controls talk to the server even while the phone is "offline". */
  bypassOffline?: boolean;
}

export async function api<T>(path: string, options: Options = {}): Promise<T> {
  if (!options.bypassOffline && isOffline()) throw new OfflineError();
  const headers: Record<string, string> = {};
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (options.token) headers["Authorization"] = `Bearer ${options.token}`;
  if (options.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;
  if (sandboxToken) headers["X-Sandbox"] = sandboxToken;
  let response: Response;
  try {
    response = await fetch(path, {
      method: options.method ?? (options.body !== undefined ? "POST" : "GET"),
      headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    });
  } catch {
    throw new OfflineError();
  }
  if (response.status === 401 && response.headers.get("X-CareLens-Sandbox") === "required") {
    setSandboxToken(null);
    sandboxLost();
  } else if (response.status === 401 && options.token) {
    signInLost(); // sign in again instead of failing on every poll
  }
  if (!response.ok) {
    let detail = `Request failed (${response.status}).`;
    try {
      const data = await response.json();
      if (typeof data.detail === "string") detail = data.detail;
    } catch {
      /* keep the generic message */
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

// --- response shapes ---------------------------------------------------------

export interface Dose {
  dose_event_id: string;
  rxcui: string;
  name: string;
  strength: string;
  directions: string;
  scheduled_at: string;
  local_time: string;
  period: "morning" | "midday" | "evening" | "night";
  status: string;
  status_label: string;
  self_reported: boolean;
  independently_verified: boolean;
  response_at: string | null;
  reminders_sent: number;
  /** Set on this phone only: a report waiting to sync. */
  pending_sync?: boolean;
}

export interface Bill {
  id: string;
  payee_label: string;
  amount: string;
  due_date: string;
  due_label: string;
  days_until_due: number;
  recurrence: string;
  status: string;
  status_label: string;
  self_reported: boolean;
  independently_verified: boolean;
  private: boolean;
}

export interface Appointment {
  id: string;
  title: string;
  location: string;
  date_label: string;
  local_time: string;
}

export interface Today {
  synthetic_notice: string | null;
  user: { id: string; display_name: string; timezone: string };
  now: string;
  local_now: string;
  date_label: string;
  clock_simulated: boolean;
  medications: Dose[];
  bills: Bill[];
  appointments: Appointment[];
  awaiting_verification: { prescription_id: string; name: string; strength: string; note: string }[];
}

export interface Source {
  source: string;
  title: string;
  url: string;
  // Reference lookups carry freshness; fixed guidance links do not.
  origin?: "live" | "bundled";
  stale?: boolean;
  fetched_at?: string;
  version?: string;
}

export interface AssistantReply {
  text: string;
  display: string;
  intent: string;
  policy: { action: string; category: string; route_to?: string };
  sources: Source[];
  focus: { dose_event_id: string; rxcui: string; name: string } | null;
  options: string[];
  tool_calls: string[];
  changed: boolean;
}

export interface Notice {
  id: string;
  kind: string;
  title: string;
  body: string;
  resource_id: string;
  delivered_at: string | null;
}

export interface CaregiverConsent {
  caregiver_id: string;
  display_name: string;
  scopes: string[];
  alerts_enabled: boolean;
  granted_at: string | null;
}

export interface Consents {
  caregivers: CaregiverConsent[];
  escalation_enabled: boolean;
  escalation_after_reminders: number;
}

export interface SeniorView {
  senior: { id: string; display_name: string };
  scopes: string[];
  alerts_enabled: boolean;
  date_label: string;
  local_now: string;
  medications?: Dose[];
  bills?: Bill[];
  appointments?: Appointment[];
  alerts: Notice[];
}

export interface Persona {
  id: string;
  display_name: string;
  role: "senior" | "caregiver" | "verifier";
}
