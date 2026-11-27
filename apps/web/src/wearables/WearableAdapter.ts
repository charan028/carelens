// One seam for every output/input device (blueprint section 4, "Meta-specific
// plan"). The app talks to `OutputRouter`; adapters hide the device.
//
//   PhoneAdapter            real: this browser's speech synthesis/recognition
//   SimulatedGlassesAdapter stand-in: models connect/disconnect, a one-line
//                           display and an audio route, in the browser
//
// IMPORTANT: SimulatedGlassesAdapter is NOT Meta's MockDeviceKit and proves
// nothing about real glasses. A Kotlin `MetaMockAdapter` backed by the Meta
// Wearables Device Access Toolkit implements this same contract in the
// Android app (see apps/android/README.md); that work is not done yet.

export interface AssistantOutput {
  /** What to say aloud. */
  speech: string;
  /** One short line for a small display. Never the full detail. */
  display: string;
}

export type WearableEventType =
  | "device.connected"
  | "device.disconnected"
  | "input.voice"
  | "display.show"
  | "audio.speak"
  | "fallback.phone";

export interface WearableEvent {
  at: number;
  adapter: string;
  type: WearableEventType;
  detail: string;
}

export interface WearableAdapter {
  readonly id: "phone" | "glasses-sim";
  readonly label: string;
  isConnected(): boolean;
  present(output: AssistantOutput): void;
  /** Resolve with what the person said. Reject if input is unavailable. */
  listen(): Promise<string>;
}

type Listener = (event: WearableEvent) => void;

export class EventLog {
  private listeners = new Set<Listener>();
  emit(adapter: string, type: WearableEventType, detail: string): void {
    const event = { at: Date.now(), adapter, type, detail };
    this.listeners.forEach((listener) => listener(event));
  }
  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => void this.listeners.delete(listener);
  }
}

// --- browser speech ----------------------------------------------------------

export interface SpeechSettings {
  enabled: boolean;
  rate: number;
}

export function speak(text: string, settings: SpeechSettings): void {
  if (!settings.enabled || !("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel(); // interruptible: a new reply stops the old one
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = settings.rate;
  window.speechSynthesis.speak(utterance);
}

/** iOS only speaks after a tap. An empty utterance on the first tap lets later reminders be heard. */
export function unlockSpeechOnFirstTap(): () => void {
  const unlock = () => {
    if ("speechSynthesis" in window) window.speechSynthesis.speak(new SpeechSynthesisUtterance(""));
  };
  window.addEventListener("pointerdown", unlock, { once: true });
  return () => window.removeEventListener("pointerdown", unlock);
}

export function stopSpeaking(): void {
  if ("speechSynthesis" in window) window.speechSynthesis.cancel();
}

type RecognitionCtor = new () => {
  lang: string;
  interimResults: boolean;
  maxAlternatives: number;
  onresult: ((event: { results: { 0: { 0: { transcript: string } } } }) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
};

function recognitionCtor(): RecognitionCtor | null {
  const scope = window as unknown as Record<string, RecognitionCtor | undefined>;
  return scope.SpeechRecognition ?? scope.webkitSpeechRecognition ?? null;
}

export function voiceInputAvailable(): boolean {
  return recognitionCtor() !== null;
}

/** Plain-language cause for each speech-recognition error the browser reports. */
const VOICE_PROBLEMS: Record<string, string> = {
  "not-allowed":
    "Microphone access is blocked. Allow the microphone for this site in your browser, or type instead.",
  "service-not-allowed":
    "This browser's speech recognition is turned off. In Safari, turn on Dictation in System Settings, " +
    "Keyboard; or use Chrome. You can also type instead.",
  "audio-capture": "No microphone was found. Check that one is connected and selected, or type instead.",
  "no-speech": "I didn't hear anything. Tap the microphone, then speak, or type instead.",
  network: "Voice input couldn't reach the browser's speech service. Check your connection, or type instead.",
  "language-not-supported": "This browser can't recognize English speech here. Type instead.",
  aborted: "Listening was interrupted. Tap the microphone to try again.",
};

function listenOnce(): Promise<string> {
  return new Promise((resolve, reject) => {
    const Ctor = recognitionCtor();
    if (!Ctor) {
      reject(new Error("Voice input isn't available in this browser. Type instead."));
      return;
    }
    stopSpeaking();
    const recognition = new Ctor();
    recognition.lang = "en-US";
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;
    let heard = "";
    recognition.onresult = (event) => {
      heard = event.results[0][0].transcript;
    };
    recognition.onerror = (event) =>
      reject(new Error(VOICE_PROBLEMS[event.error] ?? `Voice input failed (${event.error}). Type instead.`));
    recognition.onend = () => (heard ? resolve(heard) : reject(new Error("I didn't hear anything.")));
    recognition.start();
  });
}

// --- adapters ----------------------------------------------------------------

export class PhoneAdapter implements WearableAdapter {
  readonly id = "phone" as const;
  readonly label = "Phone";
  constructor(private log: EventLog, private speech: () => SpeechSettings) {}

  isConnected(): boolean {
    return true; // the phone is always the baseline
  }

  present(output: AssistantOutput): void {
    speak(output.speech, this.speech());
    this.log.emit(this.id, "audio.speak", `phone speaker: ${output.display}`);
  }

  async listen(): Promise<string> {
    const text = await listenOnce();
    this.log.emit(this.id, "input.voice", "phone microphone");
    return text;
  }
}

export class SimulatedGlassesAdapter implements WearableAdapter {
  readonly id = "glasses-sim" as const;
  readonly label = "Glasses (browser stand-in)";
  private connected = false;
  private line = "";
  private onChange: () => void = () => {};

  constructor(private log: EventLog, private speech: () => SpeechSettings) {}

  /** Lets the UI re-render when the simulated device changes. */
  bind(onChange: () => void): void {
    this.onChange = onChange;
  }

  isConnected(): boolean {
    return this.connected;
  }

  displayLine(): string {
    return this.connected ? this.line : "";
  }

  connect(): void {
    this.connected = true;
    this.line = "Connected";
    this.log.emit(this.id, "device.connected", "simulated pairing complete");
    this.onChange();
  }

  disconnect(): void {
    this.connected = false;
    this.line = "";
    this.log.emit(this.id, "device.disconnected", "simulated power-off or out of range");
    this.onChange();
  }

  present(output: AssistantOutput): void {
    if (!this.connected) throw new Error("glasses are not connected");
    this.line = output.display;
    this.log.emit(this.id, "display.show", output.display);
    // Audio really plays through this computer; the route is what's simulated.
    speak(output.speech, this.speech());
    this.log.emit(this.id, "audio.speak", "simulated glasses speaker");
    this.onChange();
  }

  async listen(): Promise<string> {
    if (!this.connected) throw new Error("The glasses are not connected. Use the phone.");
    const text = await listenOnce();
    this.log.emit(this.id, "input.voice", "simulated glasses microphone");
    return text;
  }
}

/**
 * Sends output to the glasses when they are connected and to the phone when
 * they are not. The phone screen always shows the full reply as well, so a
 * critical reminder never depends on the wearable.
 */
export class OutputRouter {
  constructor(
    private phone: WearableAdapter,
    private glasses: WearableAdapter,
    private log: EventLog,
  ) {}

  present(output: AssistantOutput, preferGlasses: boolean): "phone" | "glasses-sim" {
    if (preferGlasses) {
      if (this.glasses.isConnected()) {
        this.glasses.present(output);
        return this.glasses.id;
      }
      this.log.emit(this.phone.id, "fallback.phone", "glasses unavailable; delivered on the phone");
    }
    this.phone.present(output);
    return this.phone.id;
  }
}
