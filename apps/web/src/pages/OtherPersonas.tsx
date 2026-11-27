import { useCallback, useEffect, useState } from "react";
import { api, type SeniorView } from "../api/client";
import { clockTime, doseTone, StatusChip } from "../components/parts";

const ALL_SCOPES: Record<string, string> = { medications: "medicines", bills: "bills", appointments: "appointments" };

// --- caregiver dashboard -----------------------------------------------------

interface Summary {
  caregiver: { display_name: string };
  seniors: SeniorView[];
  notice: string;
}

export function CaregiverDashboard({ token, refreshKey }: { token: string; refreshKey: number }) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setSummary(await api<Summary>("/v1/caregiver/summary", { token, bypassOffline: true }));
      setError("");
    } catch {
      setError("The dashboard could not load. Is the API running?");
    }
  }, [token]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 4000);
    return () => window.clearInterval(timer);
  }, [load, refreshKey]);

  if (error) return <p className="notice notice-problem">{error}</p>;
  if (!summary) return <p className="empty">Loading…</p>;

  return (
    <div className="dashboard">
      <h1>{summary.caregiver.display_name.replace(" (synthetic)", "")}&rsquo;s caregiver view</h1>
      <p className="lede">{summary.notice}</p>

      {summary.seniors.length === 0 && (
        <section className="card">
          <h2>Nothing is shared with you</h2>
          <p>
            You&rsquo;ll see someone&rsquo;s list here only after they choose to share it with you from the
            Sharing screen on their phone.
          </p>
        </section>
      )}

      {summary.seniors.map((view) => {
        const hidden = Object.keys(ALL_SCOPES).filter((scope) => !view.scopes.includes(scope));
        return (
          <section key={view.senior.id} className="card">
            <header className="card-head">
              <h2>{view.senior.display_name.replace(" (synthetic)", "")}</h2>
              <p className="task-meta">
                {view.date_label}, {view.local_now} their time. Shares {view.scopes.map((s) => ALL_SCOPES[s]).join(" and ")} with you
                {hidden.length ? `; ${hidden.map((s) => ALL_SCOPES[s]).join(" and ")} not shared.` : "."}
              </p>
            </header>

            {view.alerts.length > 0 && (
              <div className="alerts">
                {view.alerts.map((alert) => (
                  <div key={alert.id} className="notice notice-reminder">
                    <div>
                      <strong>{alert.title.replace(" (synthetic)", "")}</strong>
                      <p>{alert.body}</p>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {view.medications && (
              <>
                <h3>Medicines today</h3>
                {view.medications.length === 0 && <p className="empty">None scheduled today.</p>}
                <table className="table">
                  <thead>
                    <tr><th>Time</th><th>Medicine</th><th>What we know</th><th>How we know</th></tr>
                  </thead>
                  <tbody>
                    {view.medications.map((dose) => (
                      <tr key={dose.dose_event_id}>
                        <td>{dose.local_time}</td>
                        <td>{dose.name} {dose.strength}</td>
                        <td><StatusChip tone={doseTone(dose)}>{dose.status_label}</StatusChip></td>
                        <td>
                          {dose.self_reported
                            ? `Their own report${dose.response_at ? ` at ${clockTime(dose.response_at, "America/Chicago")}` : ""}. Not independently verified.`
                            : dose.status === "scheduled"
                              ? "Not due yet."
                              : `No answer to ${dose.reminders_sent} reminder${dose.reminders_sent === 1 ? "" : "s"}. This does not mean it was missed.`}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}

            {view.bills && (
              <>
                <h3>Bills</h3>
                {view.bills.length === 0 && <p className="empty">No shared bills are coming up.</p>}
                <table className="table">
                  <tbody>
                    {view.bills.map((bill) => (
                      <tr key={bill.id}>
                        <td>{bill.due_label}</td>
                        <td>{bill.payee_label.replace(" (synthetic)", "")} {bill.amount}</td>
                        <td><StatusChip tone={bill.independently_verified || bill.self_reported ? "done" : "later"}>{bill.status_label}</StatusChip></td>
                        <td>{bill.self_reported ? "Their own report. Not confirmed by the company." : bill.independently_verified ? "Confirmed from a record." : ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}

            {view.appointments && (
              <>
                <h3>Appointments</h3>
                {view.appointments.length === 0 && <p className="empty">None in the next week.</p>}
                <ul className="plain">
                  {view.appointments.map((appointment) => (
                    <li key={appointment.id}>
                      {appointment.date_label} at {appointment.local_time}: {appointment.title.replace(" (synthetic)", "")}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </section>
        );
      })}
    </div>
  );
}

// --- pharmacist verification queue -------------------------------------------

interface RxRecord {
  prescription_id: string;
  patient: string;
  name: string;
  directions: string;
  source: string;
  record_times: string[];
  status: string;
}

export function VerifierQueue({ token, refreshKey, onChanged }: { token: string; refreshKey: number; onChanged: () => void }) {
  const [records, setRecords] = useState<RxRecord[] | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const data = await api<{ prescriptions: RxRecord[] }>("/v1/medications/verification-queue", { token, bypassOffline: true });
      setRecords(data.prescriptions);
    } catch {
      setError("The queue could not load. Is the API running?");
    }
  }, [token]);

  useEffect(() => void load(), [load, refreshKey]);

  const verify = async (record: RxRecord) => {
    try {
      await api("/v1/medications/verify", { token, body: { prescription_id: record.prescription_id }, bypassOffline: true });
      await load();
      onChanged();
    } catch (problem) {
      setError((problem as Error).message);
    }
  };

  return (
    <div className="dashboard">
      <h1>Prescription records to verify</h1>
      <p className="lede">
        CareLens schedules reminders only from records a pharmacist has checked against the source.
        Until then the medicine appears on the person&rsquo;s phone as waiting, with no reminders.
      </p>
      {error && <p className="notice notice-problem">{error}</p>}
      {records?.map((record) => (
        <section key={record.prescription_id} className="card">
          <header className="card-head">
            <h2>{record.name}</h2>
            <StatusChip tone={record.status === "active" ? "done" : "due"}>
              {record.status === "active" ? "Verified, reminders active" : "Waiting for verification"}
            </StatusChip>
          </header>
          <dl className="facts">
            <dt>For</dt><dd>{record.patient}</dd>
            <dt>Directions on the record</dt><dd>“{record.directions}”</dd>
            <dt>Times on the record</dt><dd>{record.record_times.join(", ")}</dd>
            <dt>Source</dt><dd>{record.source}</dd>
          </dl>
          {record.status === "pending_verification" && (
            <button className="button button-primary" onClick={() => void verify(record)}>
              Verify record and start reminders
            </button>
          )}
        </section>
      ))}
    </div>
  );
}
