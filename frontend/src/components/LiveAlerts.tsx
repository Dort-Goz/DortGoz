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
    box: "border-sky-700 bg-sky-950 hover:border-sky-500 hover:bg-sky-900",
    pill: "bg-sky-500 text-sky-950",
    sub: "text-sky-200/80",
    icon: "text-sky-300/70",
    close: "text-sky-300 hover:bg-sky-800",
  },
  {
    box: "border-amber-700 bg-amber-950 hover:border-amber-500 hover:bg-amber-900",
    pill: "bg-amber-500 text-amber-950",
    sub: "text-amber-200/80",
    icon: "text-amber-300/70",
    close: "text-amber-300 hover:bg-amber-800",
  },
  {
    box: "border-orange-600 bg-orange-950 hover:border-orange-400 hover:bg-orange-900",
    pill: "bg-orange-500 text-orange-950",
    sub: "text-orange-200/80",
    icon: "text-orange-300/70",
    close: "text-orange-300 hover:bg-orange-800",
  },
  {
    box: "border-red-600 bg-red-950 hover:border-red-400 hover:bg-red-900",
    pill: "bg-red-500 text-red-950",
    sub: "text-red-200/80",
    icon: "text-red-300/70",
    close: "text-red-300 hover:bg-red-800",
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
    <div className="pointer-events-none fixed bottom-3 right-[21.5rem] z-40 flex w-80 flex-col-reverse gap-1.5">
      {alerts.map((alert) => {
        const tone = TONE[severityRank(alert)];
        return (
          // Kartın tamamı anomaliyi açar; köşedeki ok yalnız işaretçidir.
          <div
            key={alert.key}
            role="button"
            tabIndex={0}
            onClick={() => onOpen(alert.key)}
            onKeyDown={(pressed) => {
              if (pressed.key !== "Enter" && pressed.key !== " ") return;
              pressed.preventDefault();
              onOpen(alert.key);
            }}
            title="Anomaliyi aç"
            className={`alert-in pointer-events-auto relative cursor-pointer border shadow-lg shadow-black/60 transition-colors ${tone.box} ${
              alert.outranksWatched ? "ring-1 ring-amber-400" : ""
            }`}
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
                onClick={(clicked) => { clicked.stopPropagation(); onDismiss(alert.key); }}
                title="Bu bildirimi gizle"
                aria-label="Bu bildirimi gizle"
                className={`shrink-0 rounded-sm px-1 text-xs leading-5 transition-colors ${tone.close}`}
              >
                ✕
              </button>
            </div>
            <p className={`truncate px-2 pb-1.5 pr-6 text-[11px] ${tone.sub}`}>
              {alert.feedLabel}
            </p>
            <span
              aria-hidden
              className={`pointer-events-none absolute bottom-1 right-1.5 text-[11px] leading-none ${tone.icon}`}
            >
              ↗
            </span>
          </div>
        );
      })}
      <button
        onClick={onMuteToggle}
        title={muted ? "Kritik olayda sesli uyarıyı aç" : "Sesli uyarıyı kapat"}
        className="pointer-events-auto self-end rounded-sm border border-zinc-700 bg-zinc-900 px-2 py-0.5 text-[10px] text-zinc-400 shadow-lg shadow-black/60 hover:border-zinc-500 hover:text-zinc-100"
      >
        {muted ? "ses kapalı" : "ses açık"}
      </button>
    </div>,
    host,
  );
}
