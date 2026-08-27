import { useCallback, useEffect, useState } from "react";
import { CANONICAL_TYPE_TR, TYPE_TR, clock } from "../lib/labels";

interface LiveClip {
  media_id: string;
  event_id: string;
  feed: string;
  category: string;
  risk: string;
  verdict: string;
  recorded_at: number;
  duration_seconds: number;
  size_bytes: number;
  clip_url: string | null;
  thumbnail_url: string | null;
  available: boolean;
}

const RISK_TR: Record<string, string> = {
  low: "Düşük", medium: "Orta", high: "Yüksek", critical: "Kritik",
  review_required: "İnceleme", undetermined: "Belirsiz",
};

const RISK_CLS: Record<string, string> = {
  low: "bg-sky-900 text-sky-200", medium: "bg-amber-900 text-amber-200",
  high: "bg-orange-900 text-orange-200", critical: "bg-red-900 text-red-200",
};

const VERDICT_TR: Record<string, string> = {
  accept: "Anomali onaylandı", reject: "Yanlış alarm", edit: "Düzeltildi",
};

export function categoryLabel(category: string): string {
  return CANONICAL_TYPE_TR[category as keyof typeof CANONICAL_TYPE_TR]
    ?? TYPE_TR[category as keyof typeof TYPE_TR]
    ?? category;
}

export function sizeLabel(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

const stamp = (epoch: number) =>
  new Date(epoch * 1000).toLocaleString("tr-TR", {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  });

export default function LiveArchive({ feedNames }: { feedNames: Record<string, string> }) {
  const [clips, setClips] = useState<LiveClip[]>([]);
  const [retention, setRetention] = useState(72);
  const [feed, setFeed] = useState("");
  const [error, setError] = useState("");
  const [playing, setPlaying] = useState("");

  const load = useCallback(async () => {
    try {
      const query = feed ? `?feed=${encodeURIComponent(feed)}` : "";
      const response = await fetch(`/api/live/clips${query}`);
      if (!response.ok) throw new Error();
      const body = await response.json();
      setClips(body.clips ?? []);
      setRetention(body.retention_hours ?? 72);
      setError("");
    } catch {
      setError("Kayıt arşivi alınamadı. Bağlantıyı denetleyin.");
    }
  }, [feed]);

  useEffect(() => {
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, [load]);

  const cameras = [...new Set(clips.map((clip) => clip.feed))].sort();

  return (
    <div className="panel flex-1">
      <div className="panel-title">
        <span>⛁ Olay Kayıtları</span>
        <span className="flex-1" />
        <label className="flex items-center gap-1 normal-case tracking-normal">
          <span className="microlabel">kamera</span>
          <select
            value={feed}
            onChange={(event) => setFeed(event.target.value)}
            className="field h-6 py-0 text-[11px]"
          >
            <option value="">tümü</option>
            {cameras.map((name) => (
              <option key={name} value={name}>{feedNames[name] || name}</option>
            ))}
          </select>
        </label>
        <span className="chip border border-zinc-700 font-mono normal-case tracking-normal text-zinc-300">
          {clips.length}
        </span>
      </div>

      <div className="shrink-0 border-b border-zinc-800 px-2 py-1.5 text-[11px] text-zinc-500">
        Kayıtlar canlı tarafta kalır. Analiz istenirse kayıt indirilir ve Analiz
        sekmesindeki “Video yükle” ile ayrıca yüklenir. Sistem bunu kendiliğinden yapmaz.
        {" "}Kayıtlar {Math.round(retention / 24)} gün sonra diskten silinir.
      </div>

      <div className="panel-body space-y-1 p-1.5 text-xs">
        {error && (
          <div className="rounded-sm border border-red-900 bg-red-950/40 px-2 py-1 text-red-200">
            {error}
          </div>
        )}
        {!error && clips.length === 0 && (
          <div className="text-zinc-500">Henüz kesilmiş olay kaydı yok.</div>
        )}
        {clips.map((clip) => (
          <div
            key={clip.media_id}
            className="space-y-1 rounded-sm border border-zinc-800 bg-zinc-950 p-1.5"
          >
            <div className="flex items-center gap-2">
              {clip.thumbnail_url ? (
                <img
                  src={clip.thumbnail_url}
                  alt=""
                  loading="lazy"
                  className="h-10 w-14 shrink-0 rounded-sm bg-black object-cover"
                />
              ) : (
                <div className="flex h-10 w-14 shrink-0 items-center justify-center rounded-sm bg-zinc-900 text-[9px] text-zinc-600">
                  yok
                </div>
              )}
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium text-zinc-200">
                  {categoryLabel(clip.category)}
                </div>
                <div className="truncate text-zinc-400">
                  {feedNames[clip.feed] || clip.feed} · {stamp(clip.recorded_at)} ·{" "}
                  <span className="font-mono">{clock(clip.duration_seconds)}</span> ·{" "}
                  {sizeLabel(clip.size_bytes)}
                </div>
                <div className="mt-0.5 flex flex-wrap gap-1">
                  <span className={`chip ${RISK_CLS[clip.risk] ?? "bg-zinc-800 text-zinc-300"}`}>
                    {RISK_TR[clip.risk] ?? clip.risk}
                  </span>
                  {clip.verdict && (
                    <span className="chip bg-zinc-800 text-zinc-300">
                      {VERDICT_TR[clip.verdict] ?? clip.verdict}
                    </span>
                  )}
                  {!clip.available && (
                    <span className="chip bg-zinc-800 text-zinc-500"
                          title="Saklama süresi doldu; karar kaydı durur, video dosyası silindi">
                      süresi doldu
                    </span>
                  )}
                </div>
              </div>
              <div className="flex shrink-0 flex-col gap-1">
                <button
                  disabled={!clip.available}
                  onClick={() => setPlaying(playing === clip.media_id ? "" : clip.media_id)}
                  className="btn btn-outline-accent h-6 px-1.5 text-[10px] disabled:border-zinc-800 disabled:text-zinc-600"
                >
                  {playing === clip.media_id ? "gizle" : "▶ oynat"}
                </button>
                <a
                  href={clip.clip_url ?? "#"}
                  download
                  aria-disabled={!clip.available}
                  title="Kaydı indir — analiz için Analiz sekmesinden ayrıca yüklenir"
                  className={`btn h-6 px-1.5 text-[10px] ${
                    clip.available ? "btn-outline" : "pointer-events-none btn-outline opacity-40"
                  }`}
                >
                  ⇩ indir
                </a>
              </div>
            </div>
            {playing === clip.media_id && clip.clip_url && (
              <video
                controls
                autoPlay
                muted
                playsInline
                poster={clip.thumbnail_url ?? undefined}
                src={clip.clip_url}
                className="w-full rounded-sm bg-black"
              />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
