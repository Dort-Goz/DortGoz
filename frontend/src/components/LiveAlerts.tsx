import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { alertLifetimeMs, severityRank, type AlertCandidate } from "../lib/liveAlerts";

export function playChime(): void {
  try {
    const Ctx = window.AudioContext
      ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "triangle";
    osc.frequency.setValueAtTime(988, ctx.currentTime);
    osc.frequency.setValueAtTime(1319, ctx.currentTime + 0.16);
    gain.gain.setValueAtTime(0.06, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.45);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.onended = () => void ctx.close();
    osc.start();
    osc.stop(ctx.currentTime + 0.45);
  } catch {
    return;
  }
}

export interface LiveAlert extends AlertCandidate {
  feedLabel: string;
  categoryLabel: string;
  outranksWatched: boolean;
}

/** Renk tüm karta işler: kenar, zemin ve seviye rozeti tek tondan gelir. */
const TONE = [
  {
    box: "border-sky-700 bg-sky-950",
    pill: "bg-sky-500 text-sky-950",
    sub: "text-sky-200/80",
    icon: "text-sky-300 hover:bg-sky-900",
  },
  {
    box: "border-amber-700 bg-amber-950",
    pill: "bg-amber-500 text-amber-950",
    sub: "text-amber-200/80",
    icon: "text-amber-300 hover:bg-amber-900",
  },
  {
    box: "border-orange-600 bg-orange-950",
    pill: "bg-orange-500 text-orange-950",
    sub: "text-orange-200/80",
    icon: "text-orange-300 hover:bg-orange-900",
  },
  {
    box: "border-red-600 bg-red-950",
    pill: "bg-red-500 text-red-950",
    sub: "text-red-200/80",
    icon: "text-red-300 hover:bg-red-900",
  },
] as const;

const LEVEL_TR = ["Düşük", "Orta", "Yüksek", "Kritik"] as const;

export default function LiveAlerts({
  alerts, onOpen, onDismiss, muted, onMuteToggle,
}: {
  alerts: LiveAlert[];
  onOpen: (key: string) => void;
  onDismiss: (key: string) => void;
  muted: boolean;
  onMuteToggle: () => void;
}) {
  // Bildirimler sağ alt köşeden yükselir; kipler her zaman üstlerinde kalır.
  const [host, setHost] = useState<HTMLElement | null>(null);
  useEffect(() => setHost(document.body), []);

  // Sayacı anahtar başına tutarız: yoklama listeyi tazeledikçe süre sıfırlanmasın.
  const timersRef = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  const dismissRef = useRef(onDismiss);
  dismissRef.current = onDismiss;

  useEffect(() => {
    const timers = timersRef.current;
    const live = new Set(alerts.map((alert) => alert.key));
    for (const alert of alerts) {
      if (timers.has(alert.key)) continue;
      timers.set(alert.key, setTimeout(() => {
        timers.delete(alert.key);
        dismissRef.current(alert.key);
      }, alertLifetimeMs(alert)));
    }
    for (const [key, timer] of timers) {
      if (live.has(key)) continue;
      clearTimeout(timer);
      timers.delete(key);
    }
  }, [alerts]);

  useEffect(() => {
    const timers = timersRef.current;
    return () => {
      for (const timer of timers.values()) clearTimeout(timer);
      timers.clear();
    };
  }, []);

  if (alerts.length === 0 || !host) return null;
  return createPortal(
    // z-40: kip pencerelerinin (z-50/z-60) altında kalmak zorunda.
    <div className="pointer-events-none fixed bottom-3 right-3 z-40 flex w-80 flex-col-reverse gap-1.5">
      {alerts.map((alert) => {
        const tone = TONE[severityRank(alert)];
        return (
          <div
            key={alert.key}
            className={`alert-in pointer-events-auto border shadow-lg shadow-black/60 ${tone.box}`}
          >
            <div className="flex items-center gap-2 px-2 py-1.5">
              <span
                className={`shrink-0 rounded-sm px-1.5 py-px text-[10px] font-bold uppercase tracking-wide ${tone.pill}`}
              >
                {LEVEL_TR[severityRank(alert)]}
              </span>
              <span className="min-w-0 flex-1 truncate text-xs font-semibold text-zinc-50">
                {alert.categoryLabel}
              </span>
              <button
                onClick={() => onOpen(alert.key)}
                title="Anomaliyi ayrı pencerede aç"
                aria-label="Anomaliyi ayrı pencerede aç"
                className={`shrink-0 rounded-sm px-1 text-sm leading-5 transition-colors ${
                  alert.outranksWatched ? "text-amber-200 hover:bg-amber-900" : tone.icon
                }`}
              >
                ↗
              </button>
              <button
                onClick={() => onDismiss(alert.key)}
                title="Bu bildirimi gizle"
                aria-label="Bu bildirimi gizle"
                className={`shrink-0 rounded-sm px-1 text-xs leading-5 transition-colors ${tone.icon}`}
              >
                ✕
              </button>
            </div>
            <p className={`truncate px-2 pb-1.5 text-[11px] ${tone.sub}`}>
              {alert.feedLabel}
            </p>
          </div>
        );
      })}
      <button
        onClick={onMuteToggle}
        title={muted ? "Kritik olayda sesli uyarıyı aç" : "Sesli uyarıyı kapat"}
        className="pointer-events-auto self-end border border-zinc-800 bg-zinc-900 px-1.5 py-0.5 text-[10px] text-zinc-500 hover:text-zinc-200"
      >
        {muted ? "ses kapalı" : "ses açık"}
      </button>
    </div>,
    host,
  );
}
