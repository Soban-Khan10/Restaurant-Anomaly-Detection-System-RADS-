// Live updates over the WebSocket /ws (contract section 5, message 3): new / closed incidents and camera
// status. Shows a toast and plays a short sound for every new incident; reconnects automatically.

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { SESSION_ENDED, useAuth } from "../auth/AuthContext";
import { useToasts } from "../components/Toasts";
import { liveSocketUrl } from "../lib/api";
import { formatTime } from "../lib/format";
import { playAlertSound } from "../lib/sound";
import type { CameraStatusMessage, LiveMessage } from "../lib/types";

export type Connection = "connecting" | "live" | "reconnecting";
type Listener = (msg: LiveMessage) => void;

interface LiveValue {
  connection: Connection;
  cameras: Record<number, CameraStatusMessage>;
  /** Run `fn` for every incident.new / incident.closed message; returns an unsubscribe function. */
  subscribe: (fn: Listener) => () => void;
  soundOn: boolean;
  setSoundOn: (on: boolean) => void;
}

const LiveContext = createContext<LiveValue | null>(null);
const SOUND_KEY = "rads.sound";
const RETRY_MS = [1000, 2000, 5000, 10000];
const PING_MS = 25_000;

function storedSound(): boolean {
  try {
    return globalThis.localStorage?.getItem(SOUND_KEY) !== "off";
  } catch {
    return true;
  }
}

export function LiveProvider({ children }: { children: ReactNode }) {
  const { token, signOut } = useAuth();
  const { push } = useToasts();
  const [connection, setConnection] = useState<Connection>("connecting");
  const [cameras, setCameras] = useState<Record<number, CameraStatusMessage>>({});
  const [soundOn, setSoundState] = useState(storedSound);
  const listeners = useRef(new Set<Listener>());
  const soundRef = useRef(soundOn);
  soundRef.current = soundOn;

  const setSoundOn = useCallback((on: boolean) => {
    setSoundState(on);
    try {
      globalThis.localStorage?.setItem(SOUND_KEY, on ? "on" : "off");
    } catch {
      /* not remembered */
    }
  }, []);

  useEffect(() => {
    if (!token) return;
    let socket: WebSocket | null = null;
    let stopped = false;
    let attempt = 0;
    let retryTimer = 0;
    let pingTimer = 0;

    const handle = (msg: LiveMessage) => {
      if (msg.type === "camera.status") {
        setCameras((c) => ({ ...c, [msg.camera_id]: msg }));
        return;
      }
      if (msg.type === "incident.new") {
        const i = msg.incident;
        push({
          title: `New incident: ${i.hazard_name}`,
          body: `${i.camera_name} · ${i.zone_name} · ${formatTime(i.start_time)}`,
          severity: i.severity,
          href: `/incidents/${i.incident_id}`,
        });
        if (soundRef.current) playAlertSound();
      }
      listeners.current.forEach((fn) => fn(msg));
    };

    const connect = () => {
      setConnection(attempt === 0 ? "connecting" : "reconnecting");
      socket = new WebSocket(liveSocketUrl(token));
      socket.onopen = () => {
        attempt = 0;
        setConnection("live");
        pingTimer = window.setInterval(() => socket?.readyState === WebSocket.OPEN && socket.send("ping"), PING_MS);
      };
      socket.onmessage = (ev) => {
        if (ev.data === "pong") return;
        try {
          handle(JSON.parse(ev.data as string) as LiveMessage);
        } catch {
          /* ignore anything that is not a contract message */
        }
      };
      socket.onclose = (ev) => {
        window.clearInterval(pingTimer);
        if (stopped) return;
        if (ev.code === 1008) {
          signOut(SESSION_ENDED); // the server refused the token
          return;
        }
        setConnection("reconnecting");
        retryTimer = window.setTimeout(connect, RETRY_MS[Math.min(attempt++, RETRY_MS.length - 1)]);
      };
    };

    connect();
    return () => {
      stopped = true;
      window.clearTimeout(retryTimer);
      window.clearInterval(pingTimer);
      socket?.close();
    };
  }, [token, push, signOut]);

  const subscribe = useCallback((fn: Listener) => {
    listeners.current.add(fn);
    return () => {
      listeners.current.delete(fn);
    };
  }, []);

  const value = useMemo(
    () => ({ connection, cameras, subscribe, soundOn, setSoundOn }),
    [connection, cameras, subscribe, soundOn, setSoundOn],
  );
  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>;
}

export function useLive(): LiveValue {
  const ctx = useContext(LiveContext);
  if (!ctx) throw new Error("useLive must be used inside <LiveProvider>");
  return ctx;
}

/** Calls `fn` (debounced) whenever an incident opens or closes. */
export function useIncidentEvents(fn: () => void, delayMs = 400): void {
  const { subscribe } = useLive();
  const fnRef = useRef(fn);
  fnRef.current = fn;
  useEffect(() => {
    let timer = 0;
    const off = subscribe((msg) => {
      if (msg.type !== "incident.new" && msg.type !== "incident.closed") return;
      window.clearTimeout(timer);
      timer = window.setTimeout(() => fnRef.current(), delayMs);
    });
    return () => {
      off();
      window.clearTimeout(timer);
    };
  }, [subscribe, delayMs]);
}
