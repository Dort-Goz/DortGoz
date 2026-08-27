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
  { edge: "border-l-sky-400", text: "text-sky-300" },
  { edge: "border-l-amber-400", text: "text-amber-300" },
  { edge: "border-l-orange-400", text: "text-orange-300" },
  { edge: "border-l-red-400", text: "text-red-300" },
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
      className={`pointer-events-none fixed z-50 flex w-72 flex-col gap-1.5 ${
        clearQueue ? "right-[21rem] top-32" : "right-3 top-12"
      }`}
    >
      {alerts.map((alert) => {
        const rank = severityRank(alert);
        return (
          <div
            key={alert.key}
            className={`pointer-events-auto alert-in border border-zinc-700 border-l-2 bg-zinc-900/95 p-2 shadow-lg shadow-black/60 ${TONE[rank].edge}`}
          >
            <div className="flex items-start gap-1.5">
              <div className="min-w-0 flex-1">
                <div className="microlabel">
                  <span className={TONE[rank].text}>{LEVEL_TR[rank]}</span> · yeni anomali
                  {alert.outranksWatched && " · izlenenden ağır"}
                </div>
                <div className="truncate text-xs font-medium text-zinc-100">{alert.title}</div>
                <div className="truncate text-[10px] text-zinc-500">
                  {alert.feedLabel} · {alert.category}
                </div>
              </div>
              <button
                onClick={() => onDismiss(alert.key)}
                title="Bu bildirimi gizle"
                className="shrink-0 px-1 text-xs text-zinc-500 hover:text-zinc-200"
              >
                ✕
              </button>
            </div>
            <button
              onClick={() => onOpen(alert.key)}
              className={`btn mt-1.5 h-6 w-full ${
                alert.outranksWatched ? "btn-outline-warn" : "btn-outline"
              }`}
            >
              {alert.outranksWatched ? "Bu olaya geç" : "Olayı aç"}
            </button>
          </div>
        );
      })}
      <button
        onClick={onMuteToggle}
        title={muted ? "Kritik olayda sesli uyarıyı aç" : "Sesli uyarıyı kapat"}
        className="pointer-events-auto self-end border border-zinc-800 bg-zinc-900/90 px-1.5 py-0.5 text-[10px] text-zinc-500 hover:text-zinc-200"
      >
        {muted ? "ses kapalı" : "ses açık"}
      </button>
    </div>
  );
}
