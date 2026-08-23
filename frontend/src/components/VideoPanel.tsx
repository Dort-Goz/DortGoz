import { useEffect, useRef } from "react";
import type { IncidentUpdate } from "../types/events";
import { clock } from "../lib/labels";

export default function VideoPanel({
  highlight, seekTo, video, feed,
}: {
  highlight: IncidentUpdate | null;
  seekTo: number | null;
  video: string | null;
  feed?: string | null;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    if (seekTo !== null && videoRef.current) videoRef.current.currentTime = seekTo;
  }, [seekTo]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const player = videoRef.current;
    if (!canvas || !player) return;

    const draw = () => {
      const ctx = canvas.getContext("2d")!;
      canvas.width = player.clientWidth;
      canvas.height = player.clientHeight;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      if (!highlight) return;
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
      for (const b of highlight.boxes) {
        const x = dx + b.x1 * dw, y = dy + b.y1 * dh;
        const w = (b.x2 - b.x1) * dw, h = (b.y2 - b.y1) * dh;
        ctx.strokeStyle = "#ef4444";
        ctx.lineWidth = 2;
        ctx.strokeRect(x, y, w, h);
        const tag = `${b.label}${b.track_id != null ? ` #${b.track_id}` : ""}`;
        ctx.font = "11px ui-monospace, monospace";
        const tagWidth = ctx.measureText(tag).width;
        ctx.fillStyle = "rgba(9, 9, 11, 0.85)";
        ctx.fillRect(x - 1, Math.max(0, y - 16), tagWidth + 8, 16);
        ctx.fillStyle = "#f87171";
        ctx.fillText(tag, x + 3, Math.max(12, y - 5));
      }
    };

    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(player);
    player.addEventListener("loadedmetadata", draw);
    return () => {
      observer.disconnect();
      player.removeEventListener("loadedmetadata", draw);
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
        {highlight && (
          <span
            className={`min-w-0 flex-1 truncate text-right font-medium normal-case tracking-normal risk-${highlight.risk}`}
            title={highlight.title}
          >
            ▸ {highlight.title} · {clock(highlight.t)}
          </span>
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
              Koşu başlatın — üst çubuktan bir kaynak seçip “Başlat”a basın.
            </span>
          </div>
        )}
        <canvas ref={canvasRef} className="pointer-events-none absolute inset-0" />
      </div>
    </div>
  );
}
