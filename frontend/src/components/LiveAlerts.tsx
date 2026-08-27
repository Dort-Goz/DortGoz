import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { severityRank, type AlertCandidate } from "../lib/liveAlerts";

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
  outranksWatched: boolean;
}

const TONE = [
  { edge: "border-l-sky-400", text: "text-sky-300" },
  { edge: "border-l-amber-400", text: "text-amber-300" },
  { edge: "border-l-orange-400", text: "text-orange-300" },
  { edge: "border-l-red-400", text: "text-red-300" },
] as const;

const LEVEL_TR = ["Düşük", "Orta", "Yüksek", "Kritik"] as const;

export const ALERT_ANCHOR_ID = "canli-bildirim-yuvasi";

export default function LiveAlerts({
  alerts, onOpen, onDismiss, muted, onMuteToggle,
}: {
  alerts: LiveAlert[];
  onOpen: (key: string) => void;
  onDismiss: (key: string) => void;
  muted: boolean;
  onMuteToggle: () => void;
}) {
  // Bildirimler canlı sekmesinden doğar: yuva o sekme düğmesinin altındadır.
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  useEffect(() => setAnchor(document.getElementById(ALERT_ANCHOR_ID)), []);

  if (alerts.length === 0 || !anchor) return null;
  return createPortal(
    <div className="flex w-80 flex-col gap-1.5">
      {alerts.map((alert) => {
        const rank = severityRank(alert);
        return (
          <div
            key={alert.key}
            className={`alert-in flex items-center gap-2 border border-zinc-700 border-l-2 bg-zinc-900 px-2 py-1.5 shadow-lg shadow-black/60 ${TONE[rank].edge}`}
          >
            <span className={`shrink-0 text-[11px] font-semibold ${TONE[rank].text}`}>
              {LEVEL_TR[rank]}
            </span>
            <span className="min-w-0 flex-1 truncate text-xs text-zinc-100">
              {alert.feedLabel}
            </span>
            <button
              onClick={() => onOpen(alert.key)}
              className={`btn h-6 shrink-0 ${
                alert.outranksWatched ? "btn-outline-warn" : "btn-outline"
              }`}
            >
              Anomaliye git
            </button>
            <button
              onClick={() => onDismiss(alert.key)}
              title="Bu bildirimi gizle"
              className="shrink-0 px-1 text-xs text-zinc-500 hover:text-zinc-200"
            >
              ✕
            </button>
          </div>
        );
      })}
      <button
        onClick={onMuteToggle}
        title={muted ? "Kritik olayda sesli uyarıyı aç" : "Sesli uyarıyı kapat"}
        className="self-end border border-zinc-800 bg-zinc-900 px-1.5 py-0.5 text-[10px] text-zinc-500 hover:text-zinc-200"
      >
        {muted ? "ses kapalı" : "ses açık"}
      </button>
    </div>,
    anchor,
  );
}
