import { useEffect, useRef } from "react";
import type { IncidentUpdate } from "../types/events";

/** Video + canvas kaplaması. Ajan seek_video/highlight_incident komutlarına tepki verir.
 *  Kaynak, koşuya alınan `/media` altındaki klip; koşu yoksa bilgi gösterilir. */
export default function VideoPanel({
  highlight, seekTo, video,
}: { highlight: IncidentUpdate | null; seekTo: number | null; video: string | null }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  // Ajan komutu: videoda konuma git
  useEffect(() => {
    if (seekTo !== null && videoRef.current) videoRef.current.currentTime = seekTo;
  }, [seekTo]);

  // Vurgulanan olayın kutularını çiz
  useEffect(() => {
    const canvas = canvasRef.current;
    const video = videoRef.current;
    if (!canvas || !video) return;
    const ctx = canvas.getContext("2d")!;
    canvas.width = video.clientWidth;
    canvas.height = video.clientHeight;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!highlight) return;
    for (const b of highlight.boxes) {
      const x = b.x1 * canvas.width, y = b.y1 * canvas.height;
      const w = (b.x2 - b.x1) * canvas.width, h = (b.y2 - b.y1) * canvas.height;
      ctx.strokeStyle = "#ef4444";
      ctx.lineWidth = 2;
      ctx.strokeRect(x, y, w, h);
      ctx.fillStyle = "#ef4444";
      ctx.font = "12px system-ui";
      const tag = `${b.label}${b.track_id != null ? ` #${b.track_id}` : ""}`;
      ctx.fillText(tag, x + 2, y - 4);
    }
  }, [highlight]);

  return (
    <div className="panel h-full relative">
      <div className="panel-title flex items-center gap-2">
        Canlı İzleme
        {highlight && (
          <span className={`ml-auto normal-case font-normal risk-${highlight.risk}`}>
            ▸ {highlight.title} (t={highlight.t}s)
          </span>
        )}
      </div>
      <div className="relative flex-1 bg-black flex items-center justify-center">
        {video ? (
          <video
            key={video}
            ref={videoRef}
            src={`/media/${video}`}
            controls
            className="max-h-full max-w-full"
          />
        ) : (
          <span className="text-xs text-zinc-600">
            Koşu başlatın — üst çubuktan bir klip seçip “Başlat”a basın.
          </span>
        )}
        <canvas ref={canvasRef} className="absolute inset-0 pointer-events-none" />
      </div>
    </div>
  );
}
