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
  title: string;
  feedLabel: string;
  category: string;
  outranksWatched: boolean;
}

const TONE = [
  "border-sky-500 bg-sky-950/90 text-sky-100 shadow-[0_0_16px_-2px_rgb(56,189,248)]",
  "border-amber-400 bg-amber-950/90 text-amber-100 shadow-[0_0_18px_-2px_rgb(251,191,36)]",
  "border-orange-400 bg-orange-950/90 text-orange-50 shadow-[0_0_22px_-2px_rgb(251,146,60)]",
  "border-red-400 bg-red-950/95 text-red-50 shadow-[0_0_28px_-1px_rgb(248,113,113)]",
] as const;

const LEVEL_TR = ["Düşük", "Orta", "Yüksek", "Kritik"] as const;

export default function LiveAlerts({
  alerts, onOpen, onDismiss, muted, onMuteToggle, clearQueue = false,
}: {
  alerts: LiveAlert[];
  onOpen: (key: string) => void;
  onDismiss: (key: string) => void;
  muted: boolean;
  onMuteToggle: () => void;
  clearQueue?: boolean;
}) {
  if (alerts.length === 0) return null;
  return (
    <div
      className={`pointer-events-none fixed top-12 z-50 flex w-72 flex-col gap-1.5 ${
        clearQueue ? "right-[21rem]" : "right-3"
      }`}
    >
      <div className="pointer-events-auto flex items-center gap-1.5 self-end">
        <button
          onClick={onMuteToggle}
          title={muted ? "Kritik olayda sesli uyarıyı aç" : "Sesli uyarıyı kapat"}
          className="btn btn-ghost h-6 px-1.5 text-[10px] text-zinc-400"
        >
          {muted ? "🔇 ses kapalı" : "🔔 ses açık"}
        </button>
      </div>
      {alerts.map((alert) => {
        const rank = severityRank(alert);
        return (
          <div
            key={alert.key}
            className={`pointer-events-auto alert-neon rounded-md border-2 p-2 ${TONE[rank]}`}
          >
            <div className="flex items-start gap-1.5">
              <span className="text-sm leading-none">⚡</span>
              <div className="min-w-0 flex-1">
                <div className="text-[10px] font-bold uppercase tracking-widest">
                  yeni anomali · {LEVEL_TR[rank]}
                  {alert.outranksWatched && " · izlenenden ağır"}
                </div>
                <div className="truncate text-xs font-medium">{alert.title}</div>
                <div className="truncate text-[10px] opacity-80">
                  {alert.feedLabel} · {alert.category}
                </div>
              </div>
              <button
                onClick={() => onDismiss(alert.key)}
                title="Bu bildirimi gizle"
                className="shrink-0 px-1 text-xs opacity-60 hover:opacity-100"
              >
                ✕
              </button>
            </div>
            <button
              onClick={() => onOpen(alert.key)}
              className={`mt-1.5 w-full rounded-sm border px-2 py-1 text-xs font-bold transition-colors ${
                alert.outranksWatched
                  ? "border-current bg-white/15 hover:bg-white/25"
                  : "border-current/50 hover:bg-white/10"
              }`}
            >
              {alert.outranksWatched ? "BU OLAYA GEÇ →" : "Olayı aç →"}
            </button>
          </div>
        );
      })}
    </div>
  );
}
