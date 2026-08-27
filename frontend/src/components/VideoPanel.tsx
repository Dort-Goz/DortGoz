import { useEffect, useRef } from "react";
import type { BoundingBox } from "../types/events";
import type { StoredIncident } from "../state";
import { clock } from "../lib/labels";

const TRACK_COLORS = [
  "#f87171", "#38bdf8", "#4ade80", "#fbbf24",
  "#c084fc", "#fb923c", "#2dd4bf", "#f472b6",
];

const BOX_FULL_S = 1.5;
const BOX_FADE_S = 3.5;

function boxColor(box: BoundingBox): string {
  if (box.track_id == null) return TRACK_COLORS[0];
  return TRACK_COLORS[Math.abs(box.track_id) % TRACK_COLORS.length];
}

function boxTag(box: BoundingBox): string {
  const track = box.track_id != null ? ` #${box.track_id}` : "";
  const conf = box.conf != null ? ` %${Math.round(box.conf * 100)}` : "";
  return `${box.label}${track}${conf}`;
}

export default function VideoPanel({
  highlight, seekTo, seekNonce, video, feed, progress, progressError, onReport,
}: {
  highlight: StoredIncident | null;
  seekTo: number | null;
  seekNonce: number;
  video: string | null;
  feed?: string | null;
  progress?: number | null;
  progressError?: boolean;
  onReport?: (t: number) => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const pendingSeek = useRef<number | null>(null);

  useEffect(() => {
    if (seekTo === null) return;
    const player = videoRef.current;
    if (player && player.readyState >= 1) {
      player.currentTime = seekTo;
      pendingSeek.current = null;
    } else {
      pendingSeek.current = seekTo;
    }
  }, [seekTo, seekNonce]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const player = videoRef.current;
    if (!canvas || !player) return;

    const draw = () => {
      const ctx = canvas.getContext("2d")!;
      canvas.width = player.clientWidth;
      canvas.height = player.clientHeight;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      if (!highlight || highlight.boxes.length === 0) return;
      const dt = Math.abs(player.currentTime - (highlight.boxT ?? highlight.t));
      if (dt >= BOX_FADE_S) return;
      const alpha = dt <= BOX_FULL_S
        ? 1
        : 1 - (dt - BOX_FULL_S) / (BOX_FADE_S - BOX_FULL_S);
      let dx = 0, dy = 0, dw = canvas.width, dh = canvas.height;
      if (player.videoWidth > 0 && player.videoHeight > 0) {
        const scale = Math.min(
          canvas.width / player.videoWidth,
          canvas.height / player.videoHeight,
        );
        dw = player.videoWidth * scale;
        dh = player.videoHeight * scale;
        dx = (canvas.width - dw) / 2;
        dy = (canvas.height - dh) / 2;
      }
      ctx.globalAlpha = alpha;
      ctx.font = "11px ui-monospace, monospace";
      for (const b of highlight.boxes) {
        const x = dx + b.x1 * dw, y = dy + b.y1 * dh;
        const w = (b.x2 - b.x1) * dw, h = (b.y2 - b.y1) * dh;
        const color = boxColor(b);
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.strokeRect(x, y, w, h);
        const tag = boxTag(b);
        const tagWidth = ctx.measureText(tag).width;
        ctx.fillStyle = "rgba(9, 9, 11, 0.85)";
        ctx.fillRect(x - 1, Math.max(0, y - 16), tagWidth + 8, 16);
        ctx.fillStyle = color;
        ctx.fillText(tag, x + 3, Math.max(12, y - 5));
      }
      ctx.globalAlpha = 1;
    };

    const onMetadata = () => {
      if (pendingSeek.current !== null) {
        player.currentTime = pendingSeek.current;
        pendingSeek.current = null;
      }
      draw();
    };

    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(player);
    player.addEventListener("loadedmetadata", onMetadata);
    player.addEventListener("timeupdate", draw);
    player.addEventListener("seeked", draw);
    return () => {
      observer.disconnect();
      player.removeEventListener("loadedmetadata", onMetadata);
      player.removeEventListener("timeupdate", draw);
      player.removeEventListener("seeked", draw);
    };
  }, [highlight, video]);

  return (
    <div className="panel h-full">
      <div className="panel-title">
        <span>Canlı İzleme</span>
        {feed && (
          <span
            className="chip border border-zinc-700 font-mono normal-case tracking-normal text-zinc-300"
            title="Bu panel seçili kamerayı oynatır — akış duvarından başka kamera seçebilirsiniz"
          >
            {feed}
          </span>
        )}
        {highlight ? (
          <span
            className={`min-w-0 flex-1 truncate text-right font-medium normal-case tracking-normal risk-${highlight.risk}`}
            title={`${highlight.title} — kutular olay anının (${clock(highlight.boxT ?? highlight.t)}) çevresinde görünür`}
          >
            ▸ {highlight.title} · {clock(highlight.t)}
          </span>
        ) : (
          <span className="flex-1" />
        )}
        {onReport && video && (
          <button
            onClick={() => onReport(videoRef.current?.currentTime ?? 0)}
            className="btn btn-outline-warn h-6 shrink-0 px-1.5 normal-case tracking-normal"
            title="Sistemin kaçırdığı bir olayı bildir — pencere oynatıcının durduğu kareye göre önerilir"
          >
            ⚑ bildir
          </button>
        )}
      </div>
      <div className="relative flex min-h-0 flex-1 items-center justify-center bg-black">
        {video ? (
          <video
            key={video}
            ref={videoRef}
            src={`/media/${video}`}
            controls
            className="h-full w-full object-contain"
          />
        ) : (
          <div className="flex flex-col items-center gap-2 px-4 text-center">
            <span className="text-3xl text-zinc-800">◉</span>
            <span className="text-xs text-zinc-600">
              Koşu başlatın — üst çubuktaki “Başlat” düğmesine basın.
            </span>
          </div>
        )}
        <canvas ref={canvasRef} className="pointer-events-none absolute inset-0" />
      </div>
      {progress != null && (
        <div className="h-1 shrink-0 bg-zinc-800" title={`analiz ilerlemesi %${progress}`}>
          <div
            className={`h-full ${progressError ? "bg-red-500" : "bg-emerald-500"}`}
            style={{ width: `${progress}%` }}
          />
        </div>
      )}
    </div>
  );
}
