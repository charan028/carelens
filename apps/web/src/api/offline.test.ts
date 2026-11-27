import { describe, expect, it } from "vitest";
import type { Dose, Today } from "./client";
import { applyQueue, dueLocalReminders, flushQueue, OfflineStore } from "./offline";

function memoryStorage() {
  const data = new Map<string, string>();
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => void data.set(key, value),
  };
}

function dose(id: string, status: string, scheduledAt: string): Dose {
  return {
    dose_event_id: id, rxcui: "314076", name: "lisinopril", strength: "10 mg",
    directions: "Take 1 tablet by mouth once daily in the morning.", scheduled_at: scheduledAt,
    local_time: "8:00 AM", period: "morning", status, status_label: status,
    self_reported: false, independently_verified: false, response_at: null, reminders_sent: 0,
  };
}

function today(doses: Dose[]): Today {
  return {
    synthetic_notice: "Synthetic demo data.", user: { id: "u", display_name: "Rosa", timezone: "America/Chicago" },
    now: "2026-11-23T13:55:00+00:00", local_now: "7:55 AM", date_label: "Monday, November 23",
    clock_simulated: true, medications: doses, bills: [], appointments: [], awaiting_verification: [],
  };
}

describe("offline store", () => {
  it("keeps the last known plan across reloads", () => {
    const storage = memoryStorage();
    new OfflineStore(storage).saveToday(today([dose("d1", "scheduled", "2026-11-23T14:00:00+00:00")]));
    const restored = new OfflineStore(storage).loadToday();
    expect(restored?.today.medications[0].name).toBe("lisinopril");
    expect(restored?.savedAt).toBe("2026-11-23T13:55:00+00:00");
  });

  it("survives corrupt storage", () => {
    const storage = memoryStorage();
    storage.setItem("carelens.today", "{not json");
    expect(new OfflineStore(storage).loadToday()).toBeNull();
  });

  it("keeps one queued answer per dose, the newest", () => {
    const store = new OfflineStore(memoryStorage());
    store.enqueue({ doseEventId: "d1", status: "unsure", at: "t1" });
    store.enqueue({ doseEventId: "d1", status: "taken", at: "t2" });
    expect(store.queue()).toHaveLength(1);
    expect(store.queue()[0].status).toBe("taken");
  });

  it("shows a queued report as self-reported and waiting to sync", () => {
    const store = new OfflineStore(memoryStorage());
    store.enqueue({ doseEventId: "d1", status: "taken", at: "t" });
    const shown = applyQueue(today([dose("d1", "reminded", "x"), dose("d2", "scheduled", "y")]), store.queue());
    expect(shown.medications[0]).toMatchObject({
      status: "self_reported_taken", self_reported: true, pending_sync: true, independently_verified: false,
    });
    expect(shown.medications[1].pending_sync).toBeUndefined();
  });
});

describe("queue flush", () => {
  it("sends each report once with its idempotency key and clears the queue", async () => {
    const store = new OfflineStore(memoryStorage());
    const queued = store.enqueue({ doseEventId: "d1", status: "taken", at: "t" });
    const keys: string[] = [];
    const result = await flushQueue(store, async (report) => void keys.push(report.key));
    expect(result).toEqual({ sent: 1, left: 0 });
    expect(keys).toEqual([queued.key]);
  });

  it("keeps reports queued while the network is still down", async () => {
    const store = new OfflineStore(memoryStorage());
    store.enqueue({ doseEventId: "d1", status: "taken", at: "t" });
    store.enqueue({ doseEventId: "d2", status: "skipped", at: "t" });
    const result = await flushQueue(store, async () => {
      throw new Error("offline");
    });
    expect(result).toEqual({ sent: 0, left: 2 });
  });

  it("drops a report the server permanently refuses", async () => {
    const store = new OfflineStore(memoryStorage());
    store.enqueue({ doseEventId: "gone", status: "taken", at: "t" });
    const result = await flushQueue(store, async () => {
      throw Object.assign(new Error("conflict"), { status: 409 });
    });
    expect(result).toEqual({ sent: 0, left: 0 });
  });
});

describe("phone-local reminders", () => {
  const plan = today([
    dose("due", "scheduled", "2026-11-23T14:00:00+00:00"),
    dose("later", "scheduled", "2026-11-24T00:00:00+00:00"),
    dose("done", "self_reported_taken", "2026-11-23T14:00:00+00:00"),
  ]);
  const at = (iso: string) => new Date(iso).getTime();

  it("fires nothing before the dose time", () => {
    expect(dueLocalReminders(plan, at("2026-11-23T13:59:00Z"), new Set())).toEqual([]);
  });

  it("fires for a due dose without any server", () => {
    const due = dueLocalReminders(plan, at("2026-11-23T14:00:30Z"), new Set());
    expect(due.map((d) => d.dose_event_id)).toEqual(["due"]);
  });

  it("does not repeat a reminder or nag about a reported dose", () => {
    expect(dueLocalReminders(plan, at("2026-11-23T15:00:00Z"), new Set(["due"]))).toEqual([]);
  });
});
