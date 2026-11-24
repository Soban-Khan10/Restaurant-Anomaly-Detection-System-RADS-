// A short two-tone chime for new incidents, made with the Web Audio API (no sound file needed).
// Browsers only allow sound after the user has interacted with the page, so the audio context is
// unlocked on the first click or key press.

let ctx: AudioContext | null = null;

function context(): AudioContext | null {
  if (ctx) return ctx;
  const Ctor = globalThis.AudioContext ?? (globalThis as unknown as { webkitAudioContext?: typeof AudioContext })
    .webkitAudioContext;
  if (!Ctor) return null;
  ctx = new Ctor();
  return ctx;
}

/** Call once at start-up; resumes audio after the first user gesture. */
export function unlockAudioOnFirstGesture(): void {
  const unlock = () => {
    void context()?.resume();
    window.removeEventListener("pointerdown", unlock);
    window.removeEventListener("keydown", unlock);
  };
  window.addEventListener("pointerdown", unlock);
  window.addEventListener("keydown", unlock);
}

export function playAlertSound(): void {
  const ac = context();
  if (!ac || ac.state !== "running") return; // not unlocked yet: stay silent rather than throw
  const start = ac.currentTime;
  [880, 660].forEach((freq, i) => {
    const osc = ac.createOscillator();
    const gain = ac.createGain();
    const t = start + i * 0.18;
    osc.type = "sine";
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.exponentialRampToValueAtTime(0.18, t + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.16);
    osc.connect(gain).connect(ac.destination);
    osc.start(t);
    osc.stop(t + 0.17);
  });
}
