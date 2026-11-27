import { describe, expect, it } from "vitest";
import {
  EventLog,
  OutputRouter,
  type AssistantOutput,
  type WearableAdapter,
  type WearableEvent,
} from "./WearableAdapter";

class FakeAdapter implements WearableAdapter {
  shown: AssistantOutput[] = [];
  constructor(public id: "phone" | "glasses-sim", public label: string, public connected: boolean) {}
  isConnected() {
    return this.connected;
  }
  present(output: AssistantOutput) {
    if (!this.connected) throw new Error("not connected");
    this.shown.push(output);
  }
  async listen() {
    return "";
  }
}

const reminder = { speech: "It is time for your medicine.", display: "8:00 AM medicine" };

function setup(glassesConnected: boolean) {
  const log = new EventLog();
  const events: WearableEvent[] = [];
  log.subscribe((event) => events.push(event));
  const phone = new FakeAdapter("phone", "Phone", true);
  const glasses = new FakeAdapter("glasses-sim", "Glasses", glassesConnected);
  return { router: new OutputRouter(phone, glasses, log), phone, glasses, events };
}

describe("output router", () => {
  it("uses the glasses when they are connected", () => {
    const { router, phone, glasses } = setup(true);
    expect(router.present(reminder, true)).toBe("glasses-sim");
    expect(glasses.shown).toEqual([reminder]);
    expect(phone.shown).toEqual([]);
  });

  it("falls back to the phone when the glasses are disconnected, and logs it", () => {
    const { router, phone, glasses, events } = setup(false);
    expect(router.present(reminder, true)).toBe("phone");
    expect(phone.shown).toEqual([reminder]);
    expect(glasses.shown).toEqual([]);
    expect(events.map((event) => event.type)).toEqual(["fallback.phone"]);
  });

  it("goes straight to the phone when glasses were not asked for", () => {
    const { router, phone, events } = setup(true);
    expect(router.present(reminder, false)).toBe("phone");
    expect(phone.shown).toHaveLength(1);
    expect(events).toEqual([]);
  });
});
