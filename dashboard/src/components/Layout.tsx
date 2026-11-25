import { useEffect, useState, useSyncExternalStore } from "react";
import { NavLink, Outlet, useLocation } from "react-router";
import { useAuth } from "../auth/AuthContext";
import { ROLE_LABEL, TIME_ZONE } from "../lib/format";
import { onSoundStateChange, playAlertSound, soundBlocked } from "../lib/sound";
import { useLive, type Connection } from "../live/LiveContext";
import { NAV, navFor } from "../nav";
import { LogoutIcon, MenuIcon, SoundOffIcon, SoundOnIcon } from "./Icons";
import { ReviewNote } from "./ui";

const connectionStyle: Record<Connection, { dot: string; text: string; label: string }> = {
  live: { dot: "bg-emerald-500 pulse-dot", text: "text-emerald-700", label: "Live" },
  connecting: { dot: "bg-slate-400", text: "text-slate-500", label: "Connecting…" },
  reconnecting: { dot: "bg-amber-500", text: "text-amber-700", label: "Reconnecting…" },
};

function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 15_000);
    return () => window.clearInterval(id);
  }, []);
  return (
    <span className="hidden text-sm tabular-nums text-slate-500 lg:inline">
      {new Intl.DateTimeFormat("en-GB", { timeZone: TIME_ZONE, weekday: "short", day: "numeric", month: "short",
        hour: "2-digit", minute: "2-digit", hour12: false }).format(now)}
    </span>
  );
}

/** Sound on/off. Switching it on plays the chime once, so you know it works. While the browser still blocks
 *  sound (page not clicked yet since it was opened), a "Click to allow sound" prompt is shown. */
function SoundControl({ soundOn, setSoundOn }: { soundOn: boolean; setSoundOn: (on: boolean) => void }) {
  const blocked = useSyncExternalStore(onSoundStateChange, soundBlocked);
  const warn = soundOn && blocked;
  return (
    <div className="flex items-center gap-1">
      {warn && (
        <button type="button" onClick={() => playAlertSound()}
          className="hidden rounded-full bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-800 ring-1 ring-amber-200
            ring-inset hover:bg-amber-100 sm:inline-flex"
          title="Browsers only play sound after you click on the page">
          Click to allow sound
        </button>
      )}
      <button
        type="button"
        onClick={() => {
          setSoundOn(!soundOn);
          if (!soundOn) playAlertSound();
        }}
        className="relative rounded-lg p-2 text-slate-600 hover:bg-slate-100"
        aria-pressed={soundOn}
        aria-label={soundOn ? "Turn alert sound off" : "Turn alert sound on"}
        title={!soundOn ? "Alert sound is off" : warn ? "Click anywhere on the page to allow the alert sound"
          : "Alert sound is on"}
      >
        {soundOn ? <SoundOnIcon className="h-5 w-5" /> : <SoundOffIcon className="h-5 w-5 text-slate-400" />}
        {warn && <span className="absolute top-1.5 right-1.5 h-2 w-2 rounded-full bg-amber-500" aria-hidden="true" />}
      </button>
    </div>
  );
}

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { user, signOut } = useAuth();
  const items = navFor(user?.role);
  return (
    <div className="flex h-full flex-col bg-slate-900 text-slate-300">
      <div className="flex items-center gap-3 px-5 py-5">
        <div className="grid h-9 w-9 place-items-center rounded-lg bg-brand-600 text-sm font-bold text-white">R</div>
        <div>
          <p className="text-sm font-semibold tracking-wide text-white">RADS</p>
          <p className="text-xs text-slate-400">Kitchen safety monitoring</p>
        </div>
      </div>
      <nav className="flex-1 space-y-1 px-3 py-2" aria-label="Main">
        {items.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            onClick={onNavigate}
            className={({ isActive }) =>
              `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition ${
                isActive ? "bg-slate-800 text-white" : "hover:bg-slate-800/60 hover:text-white"
              }`
            }
          >
            <Icon className="h-5 w-5 shrink-0" />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="border-t border-slate-800 p-4">
        <p className="truncate text-sm font-medium text-white">{user?.full_name || user?.username}</p>
        <p className="text-xs text-slate-400">{user ? ROLE_LABEL[user.role] : ""}</p>
        <button
          type="button"
          onClick={() => signOut()}
          className="mt-3 flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm text-slate-300 hover:bg-slate-800
            hover:text-white"
        >
          <LogoutIcon className="h-4 w-4" />
          Sign out
        </button>
      </div>
    </div>
  );
}

export default function Layout() {
  const { connection, soundOn, setSoundOn } = useLive();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const conn = connectionStyle[connection];
  const title =
    NAV.find((n) => (n.to === "/" ? location.pathname === "/" : location.pathname.startsWith(n.to)))?.label ??
    "RADS";

  useEffect(() => {
    document.title = `${title} · RADS`;
  }, [title]);

  return (
    <div className="min-h-screen md:pl-60">
      <aside className="fixed inset-y-0 left-0 hidden w-60 md:block">
        <Sidebar />
      </aside>

      {menuOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <button type="button" aria-label="Close menu" className="absolute inset-0 bg-slate-900/50"
            onClick={() => setMenuOpen(false)} />
          <div className="relative h-full w-64">
            <Sidebar onNavigate={() => setMenuOpen(false)} />
          </div>
        </div>
      )}

      <header className="sticky top-0 z-30 flex h-16 items-center gap-3 border-b border-slate-200 bg-white/95 px-4
        backdrop-blur sm:px-6">
        <button type="button" className="rounded-lg p-2 text-slate-600 hover:bg-slate-100 md:hidden"
          aria-label="Open menu" onClick={() => setMenuOpen(true)}>
          <MenuIcon className="h-5 w-5" />
        </button>
        <h1 className="text-lg font-semibold text-slate-900">{title}</h1>
        <div className="ml-auto flex items-center gap-4">
          <Clock />
          <span className={`flex items-center gap-2 text-sm font-medium ${conn.text}`}
            title="Connection to the RADS server for live updates">
            <span className={`h-2 w-2 rounded-full ${conn.dot}`} />
            {conn.label}
          </span>
          <SoundControl soundOn={soundOn} setSoundOn={setSoundOn} />
        </div>
      </header>

      <main className="mx-auto max-w-7xl space-y-6 px-4 py-6 sm:px-6">
        <ReviewNote />
        <Outlet />
      </main>
    </div>
  );
}
