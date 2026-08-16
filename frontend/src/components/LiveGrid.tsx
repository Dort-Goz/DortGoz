import { useCallback, useEffect, useState } from "react";
import type { IncidentUpdate } from "../types/events";
import TriagePanel from "./TriagePanel";

/** `GET /api/live/status` akış görünümü (backend FeedStatus aynası). */
interface LiveFeed {
  name: string;
  url: string;
  desc: string;            // insan-okur kamera adı (boşsa name gösterilir)
  state: string;           // baslatiliyor | akiyor | isleniyor | hata
  lag_s: number | null;
  dropped_s: number;
  segments_done: number;
  last_error: string;
  snapshot: string;
}

/** Gecikme rozeti: segment süresi 30 sn → ~45 sn'e kadar "canlıya yetişik". */
function lagBadge(f: LiveFeed): { text: string; cls: string } {
  if (f.state === "hata") return { text: "KOPUK", cls: "bg-red-700" };
  if (f.lag_s == null) return { text: "başlıyor…", cls: "bg-zinc-700" };
  const s = Math.round(f.lag_s);
  if (s <= 45) return { text: `CANLI −${s}s`, cls: "bg-emerald-700" };
  if (s <= 120) return { text: `−${s}s geride`, cls: "bg-amber-700" };
  return { text: `−${Math.round(s / 60)}dk geride`, cls: "bg-red-700" };
}

export default function LiveGrid({ incidents, onSelectFeed, onOpenTraining }: {
  /** Akış adı → olay kartları (WS'ten; hücre rozetinde sayı gösterilir) */
  incidents: Record<string, IncidentUpdate[]>;
  onSelectFeed: (feed: string) => void;
  onOpenTraining: (eventId: string) => void;
}) {
  const [feeds, setFeeds] = useState<LiveFeed[]>([]);
  const [active, setActive] = useState(false);
  const [error, setError] = useState("");
  const [zoom, setZoom] = useState<string | null>(null);
  // Kare tazeleme yoğunluğu: N segmentte 1 kare indirilir (bant genişliği
  // seçimi — yavaş istemci bağlantısında düşürülür). Sunucu her segmentte
  // anlık görüntü üretmeye devam eder; bu YALNIZ istemcinin indirme sıklığı.
  const [rate, setRate] = useState<number>(() =>
    Number(localStorage.getItem("dortgoz.canliKareOrani") || 1));
  const changeRate = (v: number) => {
    setRate(v);
    localStorage.setItem("dortgoz.canliKareOrani", String(v));
  };

  // Izgaranın nabzı: 2 sn'de bir durum tazele. Görüntüler AYRICA tazelenmez:
  // img önbellek kırıcısı segments_done'a bağlı — tarayıcı bir kareyi yalnız
  // YENİ anlık görüntü varken indirir (yoksa yavaş bağlantıda 25 img × 2 sn
  // ≈ 2 Mbps boşa akıyordu ve kareler yarım/bayat yükleniyordu).
  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const r = await fetch("/api/live/status");
        const body = await r.json();
        if (!alive) return;
        setActive(body.active);
        setFeeds(body.feeds);
      } catch { /* backend geçici kopuk — sonraki turda tekrar */ }
    };
    poll();
    const id = setInterval(poll, 2000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  const start = useCallback(async () => {
    setError("");
    const r = await fetch("/api/live/start", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    });
    if (!r.ok) setError((await r.json()).detail ?? r.statusText);
  }, []);

  const stop = useCallback(async () => {
    await fetch("/api/live/stop", { method: "POST" });
    setZoom(null);
  }, []);

  const zoomed = zoom ? feeds.find((f) => f.name === zoom) : null;

  return (
    <div className="flex-1 min-h-0 flex flex-col gap-2">
      <div className="flex items-center gap-3 text-sm">
        <button
          onClick={active ? stop : start}
          className={`rounded px-3 py-1 font-medium text-white ${
            active ? "bg-red-600 hover:bg-red-500" : "bg-emerald-600 hover:bg-emerald-500"
          }`}
        >
          {active ? "Canlıyı durdur" : "Canlı akışları başlat"}
        </button>
        {active && (
          <span className="text-zinc-400">
            {feeds.filter((f) => f.state !== "hata").length}/{feeds.length} akış ·{" "}
            {feeds.filter((f) => (f.lag_s ?? 1e9) <= 45).length} canlıya yetişik ·{" "}
            {feeds.reduce((a, f) => a + f.segments_done, 0)} segment işlendi
          </span>
        )}
        <label className="ml-auto flex items-center gap-1 text-zinc-400">
          kare tazeleme
          <select
            value={rate}
            onChange={(e) => changeRate(Number(e.target.value))}
            title="Izgara karesi kaç segmentte bir indirilsin — yavaş bağlantıda yükseltin (büyütülmüş görünüm her zaman en tazedir)"
            className="bg-zinc-800 border border-zinc-700 rounded px-1 py-0.5"
          >
            <option value={1}>her segment</option>
            <option value={2}>2 segmentte 1</option>
            <option value={4}>4 segmentte 1</option>
            <option value={8}>8 segmentte 1</option>
          </select>
        </label>
        {error && <span className="text-red-400">{error}</span>}
      </div>

      {zoomed && (
        <div className="flex gap-3 items-start rounded-lg border border-zinc-700 bg-zinc-900/80 p-2">
          <img
            src={`${zoomed.snapshot}?v=${zoomed.segments_done}`}
            alt={zoomed.name}
            className="max-h-64 rounded"
          />
          <div className="text-sm space-y-1 min-w-0">
            <div className="font-bold">{zoomed.desc || zoomed.name}</div>
            <div className={`inline-block rounded px-2 py-0.5 text-xs text-white ${lagBadge(zoomed).cls}`}>
              {lagBadge(zoomed).text}
            </div>
            <div className="text-zinc-400">
              {zoomed.segments_done} segment · {zoomed.state}
              {zoomed.dropped_s > 0 && ` · ${Math.round(zoomed.dropped_s)} sn atlandı`}
            </div>
            {zoomed.last_error && <div className="text-red-400 truncate">{zoomed.last_error}</div>}
            {(incidents[zoomed.name] ?? []).slice(-3).map((i) => (
              <div key={i.incident_id} className="text-amber-300 truncate">
                ⚠ {i.title} · risk {i.risk}
              </div>
            ))}
            <button onClick={() => setZoom(null)} className="text-zinc-500 hover:text-zinc-300">
              kapat ✕
            </button>
          </div>
        </div>
      )}

      <div className="flex-1 min-h-0 flex gap-2">
      <div
        className="flex-1 min-h-0 grid gap-1 overflow-auto"
        style={{ gridTemplateColumns: "repeat(5, minmax(0, 1fr))" }}
      >
        {feeds.map((f) => {
          const badge = lagBadge(f);
          const inc = incidents[f.name]?.length ?? 0;
          return (
            <button
              key={f.name}
              onClick={() => { setZoom(f.name); onSelectFeed(f.name); }}
              className={`relative rounded overflow-hidden border text-left ${
                inc > 0 ? "border-amber-600" : "border-zinc-800"
              } bg-black hover:border-zinc-500`}
              title={`${f.name} · ${f.state}`}
            >
              {f.snapshot ? (
                <img
                  src={`${f.snapshot}?v=${Math.floor(f.segments_done / rate)}`}
                  alt={f.name}
                  loading="lazy"
                  decoding="async"
                  className="w-full h-full object-cover opacity-90"
                />
              ) : (
                <div className="w-full h-full min-h-20 flex items-center justify-center text-zinc-600 text-xs">
                  {f.state === "hata" ? "bağlantı yok" : "bağlanıyor…"}
                </div>
              )}
              <div className="absolute top-0 left-0 right-0 flex justify-between px-1 pt-0.5 text-[10px]">
                <span className="bg-black/70 rounded px-1 truncate max-w-[60%]"
                      title={f.desc || f.name}>
                  {f.desc || f.name}
                </span>
                <span className={`rounded px-1 text-white ${badge.cls}`}>{badge.text}</span>
              </div>
              {(f.state === "isleniyor" || inc > 0 || f.dropped_s > 0) && (
                <div className="absolute bottom-0 left-0 right-0 flex gap-1 px-1 pb-0.5 text-[10px]">
                  {f.state === "isleniyor" && (
                    <span className="bg-sky-900/80 rounded px-1">⚙ işleniyor</span>
                  )}
                  {inc > 0 && (
                    <span className="bg-amber-800/90 rounded px-1">⚠ {inc} olay</span>
                  )}
                  {f.dropped_s > 0 && (
                    <span className="bg-red-900/80 rounded px-1">
                      ⏭ {Math.round(f.dropped_s)}s
                    </span>
                  )}
                </div>
              )}
            </button>
          );
        })}
        {!active && feeds.length === 0 && (
          <div className="col-span-5 flex items-center justify-center text-zinc-500 py-20">
            Canlı kip kapalı — config/live_feeds.json'daki akışlarla başlatın.
          </div>
        )}
      </div>
      {/* Nöbet kuyruğu: tespitler insan hükmüne düşer, doğrulananlar oturum
          listesine geçer (insan-döngüde karar katmanı) */}
      <TriagePanel
        onSelectFeed={onSelectFeed}
        onOpenTraining={onOpenTraining}
        feedNames={Object.fromEntries(
          feeds.filter((f) => f.desc).map((f) => [f.name, f.desc]))}
      />
      </div>
    </div>
  );
}
