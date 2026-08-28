import { useCallback, useEffect, useMemo, useState } from "react";
import { categoryLabel, clock, severityClass, severityLabel } from "../lib/labels";
import ImportPackage from "./ImportPackage";

export { categoryLabel };

interface StoredEvent {
  event_id: string;
  analysis_id: string;
  live: boolean;
  feed: string;
  source_label: string;
  category: string;
  risk: string;
  status: string;
  verdict: string;
  reviewer: string;
  note: string;
  start_time: number | null;
  peak_time: number | null;
  end_time: number | null;
  recorded_at: number;
  intervention_score: number;
  intervention_band: string;
  clip_url: string | null;
  thumbnail_url: string | null;
  evidence_count: number;
}

interface Facets {
  origins: { live: number; analysis: number };
  statuses: Record<string, number>;
  urgencies: Record<string, number>;
  risks: Record<string, number>;
  categories: Record<string, number>;
  feeds: Record<string, number>;
}

const STATUS_TR: Record<string, string> = {
  confirmed: "Doğrulandı",
  rejected: "Elendi",
  human_review: "İnsan incelemesi bekliyor",
  processing_failed: "İşlenemedi",
};

const STATUS_CLS: Record<string, string> = {
  confirmed: "bg-emerald-900 text-emerald-200",
  rejected: "chip-notr",
  human_review: "bg-amber-900 text-amber-200",
  processing_failed: "bg-red-900 text-red-200",
};

const STATUS_MARK: Record<string, string> = {
  confirmed: "✔", rejected: "✘", human_review: "⚑", processing_failed: "!",
};

const VERDICT_TR: Record<string, string> = {
  accept: "Operatör onayladı", reject: "Operatör eledi", edit: "Operatör düzeltti",
};

const stamp = (epoch: number) =>
  new Date(epoch * 1000).toLocaleString("tr-TR", {
    day: "2-digit", month: "2-digit", year: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });

const PAGE = 60;

export default function ReviewConsole({
  onReviewEvent,
  onOpenMaintenance,
  refreshToken,
}: {
  onReviewEvent?: (eventId: string) => void;
  onOpenMaintenance?: (eventId: string) => void;
  refreshToken?: number;
}) {
  const [origin, setOrigin] = useState("all");
  const [status, setStatus] = useState("all");
  const [urgency, setUrgency] = useState("all");
  const [category, setCategory] = useState("all");
  const [feed, setFeed] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [events, setEvents] = useState<StoredEvent[]>([]);
  const [facets, setFacets] = useState<Facets | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState("");
  const [openId, setOpenId] = useState("");

  const load = useCallback(async () => {
    const params = new URLSearchParams({
      origin, status, urgency, category, feed, query,
      limit: String(PAGE), offset: String(offset),
    });
    try {
      const response = await fetch(`/api/review/events?${params}`);
      if (!response.ok) throw new Error();
      const body = await response.json();
      setEvents(body.events ?? []);
      setFacets(body.facets ?? null);
      setTotal(body.total ?? 0);
      setError("");
    } catch {
      setError("Olay kayıtları alınamadı. Bağlantıyı denetleyin.");
    }
  }, [origin, status, urgency, category, feed, query, offset, refreshToken]);

  useEffect(() => {
    load();
    const id = setInterval(load, 10000);
    return () => clearInterval(id);
  }, [load]);

  useEffect(() => { setOffset(0); }, [origin, status, urgency, category, feed, query]);

  const filtersActive = origin !== "all" || status !== "all" || urgency !== "all"
    || category !== "all" || feed !== "" || query !== "";

  const open = useMemo(
    () => events.find((item) => item.event_id === openId) ?? null,
    [events, openId],
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="toolbar">
          <label className="toolbar-group">
            <span className="microlabel block">kaynak</span>
            <select value={origin} onChange={(e) => setOrigin(e.target.value)} className="field">
              <option value="all">tümü</option>
              <option value="live">canlı yayın{facets ? ` (${facets.origins.live})` : ""}</option>
              <option value="analysis">
                dosya analizi{facets ? ` (${facets.origins.analysis})` : ""}
              </option>
            </select>
          </label>
          <label className="toolbar-group">
            <span className="microlabel block">durum</span>
            <select value={status} onChange={(e) => setStatus(e.target.value)} className="field">
              <option value="all">tümü</option>
              {Object.entries(facets?.statuses ?? {}).map(([value, count]) => (
                <option key={value} value={value}>
                  {STATUS_TR[value] ?? value} ({count})
                </option>
              ))}
            </select>
          </label>
          <label className="toolbar-group">
            <span className="microlabel block">aciliyet</span>
            <select
              value={urgency}
              onChange={(e) => setUrgency(e.target.value)}
              className="field"
            >
              <option value="all">tümü</option>
              {Object.entries(facets?.urgencies ?? {}).map(([value, count]) => (
                <option key={value} value={value}>
                  {severityLabel(value)} ({count})
                </option>
              ))}
            </select>
          </label>
          <label className="toolbar-group">
            <span className="microlabel block">olay türü</span>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="field max-w-52"
            >
              <option value="all">tümü</option>
              {Object.entries(facets?.categories ?? {}).map(([value, count]) => (
                <option key={value} value={value}>
                  {categoryLabel(value)} ({count})
                </option>
              ))}
            </select>
          </label>
          <label className="toolbar-group">
            <span className="microlabel block">kamera</span>
            <select value={feed} onChange={(e) => setFeed(e.target.value)} className="field">
              <option value="">tümü</option>
              {Object.entries(facets?.feeds ?? {}).map(([value, count]) => (
                <option key={value} value={value}>{value} ({count})</option>
              ))}
            </select>
          </label>
          <label className="min-w-40 flex-1 space-y-0.5">
            <span className="microlabel block">ara</span>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              maxLength={200}
              placeholder="kaynak, tür, not veya inceleyen"
              className="field w-full"
            />
          </label>
          {filtersActive && (
            <button
              onClick={() => {
                setOrigin("all"); setStatus("all"); setUrgency("all");
                setCategory("all"); setFeed(""); setQuery("");
              }}
              className="btn btn-ghost"
            >
              ✕ süzgeçleri temizle
            </button>
          )}
          <span
            title="Süzgeçlere uyan toplam kayıt"
            className="chip border border-zinc-700 font-mono text-zinc-300"
          >
            {total} kayıt
          </span>
          <ImportPackage />
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,3fr)_minmax(22rem,2fr)] gap-1.5 p-1.5 text-sm">
        <div className="panel">
          <div className="panel-title"><span>Kayıtlar</span></div>
          <div className="panel-body space-y-1 p-1.5 text-xs">
            {error && (
              <div className="rounded-sm border border-red-900 bg-red-950/40 px-2 py-1 text-red-200">
                {error}
              </div>
            )}
            {!error && events.length === 0 && (
              <div className="text-zinc-500">
                {filtersActive ? "Süzgeçlere uyan kayıt yok." : "Henüz kayıtlı olay yok."}
              </div>
            )}
            {events.map((item) => (
              <button
                key={item.event_id}
                onClick={() => setOpenId(item.event_id)}
                className={`flex w-full items-center gap-2 rounded-sm border bg-zinc-950 p-1.5 text-left transition-colors ${
                  openId === item.event_id
                    ? "border-sky-700"
                    : "border-zinc-800 hover:border-zinc-600"
                }`}
              >
                {item.thumbnail_url ? (
                  <img
                    src={item.thumbnail_url}
                    alt=""
                    loading="lazy"
                    className="h-9 w-13 shrink-0 rounded-sm bg-black object-cover"
                  />
                ) : (
                  <div className="flex h-9 w-13 shrink-0 items-center justify-center rounded-sm bg-zinc-900 text-[9px] text-zinc-600">
                    —
                  </div>
                )}
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium text-zinc-200">
                    {categoryLabel(item.category)}
                  </div>
                  <div className="truncate text-zinc-500">
                    {item.source_label} · {stamp(item.recorded_at)}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <span className="chip chip-notr">{item.live ? "canlı" : "dosya"}</span>
                  <span
                    className={`chip w-20 justify-center ${severityClass(item.intervention_band)}`}
                    title="Müdahale önceliği"
                  >
                    <span className="font-mono">{item.intervention_score}</span>{" "}
                    · {severityLabel(item.intervention_band)}
                  </span>
                  <span
                    className={`chip w-4 justify-center ${STATUS_CLS[item.status] ?? "chip-notr"}`}
                    title={STATUS_TR[item.status] ?? item.status}
                  >
                    {STATUS_MARK[item.status] ?? "·"}
                  </span>
                </div>
              </button>
            ))}
            {total > PAGE && (
              <div className="flex items-center justify-between pt-1 text-zinc-500">
                <button
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - PAGE))}
                  className="btn btn-sm btn-outline disabled:opacity-40"
                >
                  ← önceki
                </button>
                <span className="font-mono">
                  {offset + 1}–{Math.min(offset + PAGE, total)} / {total}
                </span>
                <button
                  disabled={offset + PAGE >= total}
                  onClick={() => setOffset(offset + PAGE)}
                  className="btn btn-sm btn-outline disabled:opacity-40"
                >
                  sonraki →
                </button>
              </div>
            )}
          </div>
        </div>

        <div className="panel">
          <div className="panel-title">
            <span className="flex-1 truncate">
              {open ? categoryLabel(open.category) : "Ayrıntı"}
            </span>
            {open && (
              <span className="chip border border-zinc-700 font-mono normal-case tracking-normal text-zinc-400">
                {open.live ? open.feed : "dosya"}
              </span>
            )}
          </div>
          <div className="panel-body space-y-2 p-2 text-xs">
            {!open && (
              <div className="text-zinc-500">Soldan bir kayıt seçin.</div>
            )}
            {open && (
              <>
                {open.clip_url ? (
                  <video
                    controls
                    playsInline
                    preload="metadata"
                    poster={open.thumbnail_url ?? undefined}
                    src={open.clip_url}
                    className="w-full rounded-sm bg-black"
                  />
                ) : (
                  <div className="flex h-32 items-center justify-center rounded-sm border border-dashed border-zinc-800 bg-black text-zinc-500">
                    Bu kaydın klibi diskte yok.
                  </div>
                )}
                <div className="flex flex-wrap gap-1">
                  <span className={`chip ${severityClass(open.intervention_band)}`}>
                    öncelik <span className="font-mono">{open.intervention_score}</span> ·{" "}
                    {severityLabel(open.intervention_band)}
                  </span>
                  {open.risk !== "undetermined" && (
                    <span className={`chip ${severityClass(open.risk)}`}>
                      {severityLabel(open.risk)}
                    </span>
                  )}
                  <span className={`chip ${STATUS_CLS[open.status] ?? "chip-notr"}`}>
                    {STATUS_TR[open.status] ?? open.status}
                  </span>
                  {open.evidence_count > 0 && (
                    <span className="chip chip-notr">{open.evidence_count} kanıt karesi</span>
                  )}
                </div>
                <dl className="space-y-0.5 text-zinc-400">
                  <div className="flex gap-2">
                    <dt className="w-24 shrink-0 text-zinc-500">kaynak</dt>
                    <dd className="min-w-0 truncate">{open.source_label}</dd>
                  </div>
                  <div className="flex gap-2">
                    <dt className="w-24 shrink-0 text-zinc-500">kayıt anı</dt>
                    <dd className="font-mono">{stamp(open.recorded_at)}</dd>
                  </div>
                  {open.start_time != null && open.end_time != null && (
                    <div className="flex gap-2">
                      <dt className="w-24 shrink-0 text-zinc-500">olay penceresi</dt>
                      <dd className="font-mono text-sky-300">
                        {clock(open.start_time)}–{clock(open.end_time)}
                      </dd>
                    </div>
                  )}
                  {open.verdict && (
                    <div className="flex gap-2">
                      <dt className="w-24 shrink-0 text-zinc-500">karar</dt>
                      <dd>
                        {VERDICT_TR[open.verdict] ?? open.verdict}
                        {open.reviewer && ` · ${open.reviewer}`}
                      </dd>
                    </div>
                  )}
                  {open.note && (
                    <div className="flex gap-2">
                      <dt className="w-24 shrink-0 text-zinc-500">not</dt>
                      <dd className="min-w-0">{open.note}</dd>
                    </div>
                  )}
                  <div className="flex gap-2">
                    <dt className="w-24 shrink-0 text-zinc-500">olay kimliği</dt>
                    <dd className="min-w-0 truncate font-mono text-[10px]">{open.event_id}</dd>
                  </div>
                </dl>
                <div className="flex flex-wrap gap-1 border-t border-zinc-800 pt-2">
                  {open.clip_url && (
                    <a href={open.clip_url} download className="btn btn-sm btn-outline">
                      ↓ kaydı indir
                    </a>
                  )}
                  {onReviewEvent && (
                    <button
                      onClick={() => onReviewEvent(open.event_id)}
                      className="btn btn-sm btn-outline-accent"
                      title={open.verdict
                        ? "İnsan kararını yeni bir revizyonla değiştir"
                        : "Bu kayıt için insan kararını ver"}
                    >
                      {open.verdict ? "Kararı değiştir" : "Karar ver"}
                    </button>
                  )}
                  {open.verdict && onOpenMaintenance && (
                    <button
                      onClick={() => onOpenMaintenance(open.event_id)}
                      className="btn btn-outline h-6 px-2"
                      title="İnsan kararı verilmiş kaydı Bakım ekranında aç"
                    >
                      Bakımda aç →
                    </button>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
