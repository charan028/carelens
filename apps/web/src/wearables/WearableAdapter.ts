// One seam for every output/input device (blueprint section 4, "Meta-specific
// plan"). The app talks to a `WearableAdapter`; today the only one is the
// phone, backed by this browser's speech synthesis and recognition. A Kotlin
// `MetaMockAdapter` backed by the Meta Wearables Device Access Toolkit will
// implement this same contract in the Android app (see apps/android/README.md);
// that work is not done yet.

export interface AssistantOutput {
  /** What to say aloud. */
  speech: string;
  /** One short line for a small display. Never the full detail. */
  display: string;
}

export interface WearableAdapter {
  readonly id: "phone";
  readonly label: string;
  isConnected(): boolean;
  present(output: AssistantOutput): void;
  /** Resolve with what the person said. Reject if input is unavailable. */
  listen(onInterim?: (text: string) => void): Promise<string>;
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

type Recognition = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  onresult: ((event: { results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }> }) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
};
type RecognitionCtor = new () => Recognition;

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

// Older adults pause mid-sentence; the browser's default stops at the first pause.
// ponytail: fixed pauses; make them settings if real users need longer.
const PAUSE_MS = 2000; // silence after the last words before we stop
const MAX_LISTEN_MS = 15000; // hard cap, so a noisy room can't keep the mic open

let active: Recognition | null = null;

/** Ends the current listen early (the mic button while listening). */
export function stopListening(): void {
  active?.stop();
}

function listenOnce(onInterim?: (text: string) => void): Promise<string> {
  return new Promise((resolve, reject) => {
    const Ctor = recognitionCtor();
    if (!Ctor) {
      reject(new Error("Voice input isn't available in this browser. Type instead."));
      return;
    }
    stopSpeaking();
    active?.stop();
    const recognition = new Ctor();
    active = recognition;
    recognition.lang = "en-US";
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;

    let heard = "";
    let failure: Error | null = null;
    let quiet = 0;
    const finish = () => recognition.stop();
    const cap = window.setTimeout(finish, MAX_LISTEN_MS);

    recognition.onresult = (event) => {
      heard = Array.from(event.results, (result) => result[0].transcript).join(" ").replace(/\s+/g, " ").trim();
      onInterim?.(heard);
      window.clearTimeout(quiet);
      quiet = window.setTimeout(finish, PAUSE_MS);
    };
    recognition.onerror = (event) => {
      if (event.error === "no-speech" && heard) return; // a long pause after words: not an error
      failure = new Error(VOICE_PROBLEMS[event.error] ?? `Voice input failed (${event.error}). Type instead.`);
    };
    recognition.onend = () => {
      window.clearTimeout(cap);
      window.clearTimeout(quiet);
      if (active === recognition) active = null;
      if (heard) resolve(heard);
      else reject(failure ?? new Error("I didn't hear anything. Tap the microphone, then speak, or type instead."));
    };
    recognition.start();
  });
}

// --- the phone ---------------------------------------------------------------

export class PhoneAdapter implements WearableAdapter {
  readonly id = "phone" as const;
  readonly label = "Phone";
  constructor(private speech: () => SpeechSettings) {}

  isConnected(): boolean {
    return true; // the phone is always the baseline
  }

  present(output: AssistantOutput): void {
    speak(output.speech, this.speech());
  }

  listen(onInterim?: (text: string) => void): Promise<string> {
    return listenOnce(onInterim);
  }
}
