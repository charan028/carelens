// Phone-local fallback: the last known day plan, a queue of self-reports made
// without a network, and reminder timing that needs no server.
//
// Everything here is pure or takes its storage as an argument, so it is
// tested without a browser (see offline.test.ts).

import type { Dose, Today } from "./client";

export interface KeyValueStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export interface QueuedReport {
  /** Sent as Idempotency-Key, so a replay after reconnect applies once. */
  key: string;
  doseEventId: string;
  status: "taken" | "skipped" | "unsure";
  at: string;
}

const TODAY_KEY = "carelens.today";
const QUEUE_KEY = "carelens.reportQueue";
const AWAITING = ["scheduled", "reminded", "overdue"];

const LOCAL_LABEL: Record<QueuedReport["status"], [string, string]> = {
  taken: ["self_reported_taken", "Self-reported taken"],
  skipped: ["skipped", "Self-reported skipped"],
  unsure: ["unknown", "Unconfirmed"],
};

export class OfflineStore {
  constructor(private storage: KeyValueStore) {}

  saveToday(today: Today): void {
    this.write(TODAY_KEY, { savedAt: today.now, today });
  }

  loadToday(): { savedAt: string; today: Today } | null {
    return this.read(TODAY_KEY);
  }

  queue(): QueuedReport[] {
    return this.read<QueuedReport[]>(QUEUE_KEY) ?? [];
  }

  enqueue(report: Omit<QueuedReport, "key">): QueuedReport {
    const queued: QueuedReport = {
      ...report,
      key: `phone-${report.doseEventId}-${Date.now().toString(36)}`,
    };
    // A newer answer for the same dose replaces the older one.
    const rest = this.queue().filter((item) => item.doseEventId !== report.doseEventId);
    this.write(QUEUE_KEY, [...rest, queued]);
    return queued;
  }

  remove(key: string): void {
    this.write(QUEUE_KEY, this.queue().filter((item) => item.key !== key));
  }

  private read<T>(key: string): T | null {
    try {
      const raw = this.storage.getItem(key);
      return raw ? (JSON.parse(raw) as T) : null;
    } catch {
      return null; // storage can be unavailable or hold junk; start clean
    }
  }

  private write(key: string, value: unknown): void {
    try {
      this.storage.setItem(key, JSON.stringify(value));
    } catch {
      /* private mode or full storage: the app still works online */
    }
  }
}

/** Show queued reports on top of the last known plan. */
export function applyQueue(today: Today, queue: QueuedReport[]): Today {
  if (queue.length === 0) return today;
  const byDose = new Map(queue.map((item) => [item.doseEventId, item]));
  return {
    ...today,
    medications: today.medications.map((dose) => {
      const queued = byDose.get(dose.dose_event_id);
      if (!queued) return dose;
      const [status, label] = LOCAL_LABEL[queued.status];
      return {
        ...dose,
        status,
        status_label: label,
        self_reported: true,
        response_at: queued.at,
        pending_sync: true,
      };
    }),
  };
}

/** Send queued reports. Anything that fails stays queued for the next try. */
export async function flushQueue(
  store: OfflineStore,
  send: (report: QueuedReport) => Promise<void>,
): Promise<{ sent: number; left: number }> {
  let sent = 0;
  for (const report of store.queue()) {
    try {
      await send(report);
      store.remove(report.key);
      sent += 1;
    } catch (error) {
      // A 4xx will never succeed (for example the dose was discontinued), so
      // drop it. Anything else is treated as "still offline".
      const status = (error as { status?: number }).status;
      if (typeof status === "number" && status >= 400 && status < 500) {
        store.remove(report.key);
        continue;
      }
      break;
    }
  }
  return { sent, left: store.queue().length };
}

/** Doses whose time has come and that this phone has not announced yet. */
export function dueLocalReminders(today: Today, nowMs: number, announced: Set<string>): Dose[] {
  return today.medications.filter(
    (dose) =>
      AWAITING.includes(dose.status) &&
      new Date(dose.scheduled_at).getTime() <= nowMs &&
      !announced.has(dose.dose_event_id),
  );
}
