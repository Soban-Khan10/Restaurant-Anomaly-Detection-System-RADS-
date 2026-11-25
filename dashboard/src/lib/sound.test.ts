import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// A stand-in for the browser's AudioContext: `allowed` says whether the browser lets sound start right now.
let allowed = false;
let contexts: FakeAudioContext[] = [];

class FakeAudioContext extends EventTarget {
  state: AudioContextState;
  currentTime = 0;
  destination = {};
  tones = 0;
  constructor() {
    super();
    this.state = allowed ? "running" : "suspended";
    contexts.push(this);
  }
  resume(): Promise<void> {
    if (!allowed) return new Promise(() => {}); // the browser waits for a click
    this.state = "running";
    this.dispatchEvent(new Event("statechange"));
    return Promise.resolve();
  }
  pause() {
    this.state = "suspended";
    this.dispatchEvent(new Event("statechange"));
  }
  createOscillator() {
    this.tones++;
    return { type: "", frequency: { value: 0 }, connect: (g: unknown) => g, start() {}, stop() {} };
  }
  createGain() {
    return { gain: { setValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect: (d: unknown) => d };
  }
}

let page: EventTarget;
let sound: typeof import("./sound");
const activation = { hasBeenActive: false };
const click = () => {
  allowed = true; // a click lets the page play sound from now on
  activation.hasBeenActive = true;
  page.dispatchEvent(new Event("pointerdown"));
};
const tones = () => contexts.reduce((n, c) => n + c.tones, 0);
const flush = () => new Promise((r) => setTimeout(r, 0));

beforeEach(async () => {
  allowed = false;
  contexts = [];
  activation.hasBeenActive = false;
  page = new EventTarget();
  vi.stubGlobal("window", page);
  vi.stubGlobal("AudioContext", FakeAudioContext);
  vi.stubGlobal("navigator", { userActivation: activation });
  vi.resetModules();
  sound = await import("./sound");
  sound.unlockAudioOnGesture();
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("alert chime", () => {
  it("is silent and reported as blocked until the page is clicked", async () => {
    sound.playAlertSound();
    await flush();
    expect(tones()).toBe(0);
    expect(sound.soundBlocked()).toBe(true);
  });

  it("plays after the first click, and tells the header", async () => {
    const changed = vi.fn();
    sound.onSoundStateChange(changed);
    click();
    expect(sound.soundBlocked()).toBe(false);
    expect(changed).toHaveBeenCalled();
    sound.playAlertSound();
    expect(tones()).toBe(2); // two notes
  });

  it("comes back by itself if the browser pauses it later (e.g. after the computer slept)", async () => {
    click();
    contexts[0].pause();
    expect(sound.soundBlocked()).toBe(true);
    sound.playAlertSound();
    await flush();
    expect(tones()).toBe(2);
    expect(sound.soundBlocked()).toBe(false);
  });

  it("every click resumes paused sound, not only the first one", () => {
    click();
    contexts[0].pause();
    page.dispatchEvent(new Event("keydown"));
    expect(contexts[0].state).toBe("running");
  });

  it("an incident that arrived while sound was blocked does not chime later on a click", async () => {
    sound.playAlertSound(); // blocked: dropped
    click();
    await flush();
    expect(tones()).toBe(0);
  });
});
