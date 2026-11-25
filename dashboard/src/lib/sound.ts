// A short two-tone chime for new incidents, made with the Web Audio API (no sound file needed).
// Browsers only allow sound after the user has clicked or typed on the page, and may pause it again later
// (e.g. after the computer sleeps). So every click or key press turns sound back on if it is paused, and the
// header shows "Click to allow sound" while the browser is still blocking it.

let ctx: AudioContext | null = null;
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((fn) => fn());

function context(): AudioContext | null {
  if (ctx && ctx.state !== "closed") return ctx;
  const Ctor = globalThis.AudioContext ?? (globalThis as unknown as { webkitAudioContext?: typeof AudioContext })
    .webkitAudioContext;
  if (!Ctor) return null;
  try {
    ctx = new Ctor();
  } catch {
    return null;
  }
  ctx.addEventListener("statechange", notify);
  notify();
  return ctx;
}

/** True while the browser is not playing sound for this page (nobody has clicked or typed on it yet). */
export function soundBlocked(): boolean {
  return !ctx || ctx.state !== "running";
}

/** Run `fn` whenever soundBlocked() may have changed; returns an unsubscribe function. */
export function onSoundStateChange(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/** Call once at start-up. Every click or key press resumes sound if the browser has paused it.
 *  Capture phase, so a dialog that stops a key press from bubbling cannot get in the way. */
export function unlockAudioOnGesture(): void {
  const resume = () => {
    const ac = context();
    if (ac && ac.state !== "running") ac.resume().catch(() => {});
  };
  window.addEventListener("pointerdown", resume, { capture: true, passive: true });
  window.addEventListener("keydown", resume, { capture: true, passive: true });
}

function chime(ac: AudioContext): void {
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

const LATE_MS = 1500; // a chime that could not start within this time is dropped, never played late

export function playAlertSound(): void {
  const ac = context();
  if (!ac) return;
  if (ac.state === "running") {
    chime(ac);
    return;
  }
  // Paused. Resuming without a click is allowed once the page has been clicked before (e.g. after the computer
  // slept). Without that it would wait for the next click, so don't try: that chime would come much later.
  const activated = (globalThis.navigator as Navigator & { userActivation?: { hasBeenActive: boolean } })
    ?.userActivation?.hasBeenActive;
  if (!activated) return;
  const asked = performance.now();
  ac.resume()
    .then(() => {
      if (ac.state === "running" && performance.now() - asked < LATE_MS) chime(ac);
    })
    .catch(() => {});
}
