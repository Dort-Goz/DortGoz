import { useCallback, useEffect, useState } from "react";
import type { EventEvidenceRef } from "../types/events";

interface SuggestedAction {
  action: string;
  label: string;
  status: "available" | "pending" | "prepared" | "rejected" | "failed";
  request_id: string | null;
}

interface TriageItem {
  key: string;
  feed: string;
  run_id: string;
  video: string;
  incident_id: string;
  t: number;
  wall: number;
  title: string;
  model_category: string;
  risk: string;
  phase: string;
  thumbnail: string | null;
  evidence: EventEvidenceRef[];
  needs_review: boolean;
  review_reason: string;
  verdict: string;
  operator_category: string;
  note: string;
  decided_wall: number | null;
  tekrar: number;
  model_start: number | null;
  model_end: number | null;
  operator_start: number | null;
  operator_end: number | null;
  suggested_actions?: SuggestedAction[];
}

export interface Decision {
  key: string;
  verdict: string;
  category?: string;
  reviewer?: string;
  note?: string;
  operator_start?: number | null;
  operator_end?: number | null;
}

interface Snapshot {
  pending: TriageItem[];
  confirmed: TriageItem[];
  dismissed_count: number;
  auto_dismissed: number;
  rules: { feed: string; category: string; auto_count: number }[];
  categories: string[];
}

function humanizeReason(reason: string): string {
  return reason
    .split(" · ")
    .map((part) => {
      if (/provisional|automatic confirmation|VALIDATED|Runtime evidence/i.test(part))
        return "Sistem kanıtı doğruladı; otomatik onay kapalı — karar sizde.";
      if (/model belirsizlik bildirdi:/i.test(part))
        return part.replace(/model belirsizlik bildirdi:/i, "Model emin değil:");
      return part;
    })
    .join(" ");
}

const CATEGORY_TR: Record<string, string> = {
  kavga: "Kavga", saldiri: "Saldırı", hirsizlik: "Hırsızlık",
  silahli_olay: "Silahlı olay", yangin: "Yangın", patlama: "Patlama",
  arac_kazasi: "Araç kazası", vandalizm: "Vandalizm",
  bilinmeyen: "Sınıflandırılamayan",
};

const RISK_CLS: Record<string, string> = {
  dusuk: "bg-sky-900 text-sky-200", orta: "bg-amber-900 text-amber-200",
  yuksek: "bg-orange-900 text-orange-200", kritik: "bg-red-900 text-red-200",
};

const clock = (t: number) =>
  `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(Math.floor(t % 60)).padStart(2, "0")}`;

export function parseClock(text: string): number | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const parts = trimmed.split(":");
  if (parts.length > 2) return null;
  const nums = parts.map((p) => Number(p));
  if (nums.some((n) => !Number.isFinite(n) || n < 0)) return null;
  const seconds = nums.length === 2 ? nums[0] * 60 + nums[1] : nums[0];
  return Number.isFinite(seconds) ? seconds : null;
}

const REVIEWER_KEY = "dortgoz.operator";
const wallClock = (epoch: number) =>
  new Date(epoch * 1000).toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit" });

export function selectReviewEvidence(
  evidence: EventEvidenceRef[], peak: number, limit = 3,
): EventEvidenceRef[] {
  const unique = [...evidence]
    .sort((a, b) => a.timestamp - b.timestamp)
    .filter((item, index, all) =>
      index === all.findIndex((other) =>
        other.frame_id === item.frame_id && other.timestamp === item.timestamp));
  if (unique.length <= limit) return unique;
  const closest = unique.reduce((best, item) =>
    Math.abs(item.timestamp - peak) < Math.abs(best.timestamp - peak) ? item : best);
  const selected = [unique[0], closest, unique[unique.length - 1]];
  for (const item of unique) {
    if (selected.length >= limit) break;
    if (!selected.includes(item)) selected.push(item);
  }
  return [...new Set(selected)].slice(0, limit).sort((a, b) => a.timestamp - b.timestamp);
}

export function evidenceFrameUrl(key: string, timestamp: number): string {
  const query = new URLSearchParams({ key, timestamp: String(timestamp) });
  return `/api/triage/evidence-frame?${query.toString()}`;
}

function PendingCard({ item, categories, feedLabel, onDecide, onSeek }: {
  item: TriageItem;
  categories: string[];
  feedLabel: string;
  onDecide: (d: Decision) => void;
  onSeek?: (feed: string, timestamp: number, video: string) => void;
}) {
  const [cat, setCat] = useState(
    categories.includes(item.model_category) ? item.model_category : "bilinmeyen");
  const [editTimes, setEditTimes] = useState(false);
  const [startText, setStartText] = useState(clock(item.model_start ?? item.t));
  const [endText, setEndText] = useState(clock(item.model_end ?? item.t));
  const [note, setNote] = useState("");

  const start = parseClock(startText);
  const end = parseClock(endText);
  const timesValid = !editTimes || (start !== null && end !== null && start <= end);

  const confirm = () =>
    onDecide({
      key: item.key,
      verdict: "anomali",
      category: cat,
      note,
      operator_start: editTimes ? start : null,
      operator_end: editTimes ? end : null,
    });

  return (
    <div className="rounded border border-zinc-700 bg-zinc-900/70 p-2 space-y-1.5 text-xs">
      <div className="flex items-center gap-2">
        {item.thumbnail && (
          <img src={item.thumbnail} alt="" className="w-14 h-10 object-cover rounded" />
        )}
        <div className="min-w-0">
          <div className="font-medium truncate">{item.title}</div>
          <div className="text-zinc-400">
            {feedLabel} · video {clock(item.t)} · {wallClock(item.wall)}
          </div>
          <div className="flex gap-1 mt-0.5">
            <span className={`rounded px-1 ${RISK_CLS[item.risk] ?? "bg-zinc-800"}`}>
              {item.risk}
            </span>
            <span className="rounded px-1 bg-zinc-800 text-zinc-300">
              model: {CATEGORY_TR[item.model_category] ?? item.model_category}
            </span>
            {item.tekrar > 1 && (
              <span className="rounded px-1 bg-indigo-900 text-indigo-200"
                    title="Aynı kameradan aynı sınıfta tekrar tespit — tek kartta birleştirildi">
                ×{item.tekrar}
              </span>
            )}
          </div>
        </div>
        {onSeek && (
          <button
            onClick={() => onSeek(item.feed, item.t, item.video)}
            className="ml-auto shrink-0 rounded border border-sky-800 px-1.5 py-1 text-[10px] text-sky-300 hover:bg-sky-950/50"
            title="Videoyu olay anına götür"
          >
            ▶ videoda aç
          </button>
        )}
      </div>
      {item.evidence.length > 0 && (
        <div>
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-zinc-500">
            Doğrulanmış video kanıtı
          </div>
          <div className="grid grid-cols-3 gap-1">
            {selectReviewEvidence(item.evidence, item.t).map((evidence) => (
              <button
                key={`${evidence.frame_id}:${evidence.timestamp}`}
                onClick={() => onSeek?.(item.feed, evidence.timestamp, item.video)}
                className="overflow-hidden rounded border border-zinc-800 bg-zinc-950 text-left hover:border-sky-700"
                title={`${clock(evidence.timestamp)} · ${evidence.claim}`}
              >
                <img
                  src={evidenceFrameUrl(item.key, evidence.timestamp)}
                  alt={`${clock(evidence.timestamp)} kanıt karesi`}
                  loading="lazy"
                  className="aspect-video w-full bg-black object-cover"
                />
                <div className="px-1 py-0.5">
                  <div className="font-mono text-[9px] text-sky-300">
                    {clock(evidence.timestamp)}
                  </div>
                  <div className="line-clamp-2 text-[9px] leading-tight text-zinc-400">
                    {evidence.claim}
                  </div>
                </div>
              </button>
            ))}
          </div>
        </div>
      )}
      {item.needs_review && item.review_reason && (
        <div className="text-amber-300/90" title={item.review_reason}>
          ? {humanizeReason(item.review_reason)}
        </div>
      )}
      <div className="flex items-center gap-1 text-[10px]">
        <button
          onClick={() => setEditTimes((v) => !v)}
          className={`rounded px-1 py-0.5 border ${
            editTimes
              ? "border-sky-700 text-sky-300 bg-sky-950/40"
              : "border-zinc-700 text-zinc-400 hover:border-zinc-500"
          }`}
          title="Olayın gerçek başlangıç/bitiş anını düzelt — zamansal doğruluk ölçümünün referansı olur"
        >
          ⏱ süre düzelt
        </button>
        {!editTimes && (item.model_start !== null || item.model_end !== null) && (
          <span className="text-zinc-500">
            model: {clock(item.model_start ?? item.t)}–{clock(item.model_end ?? item.t)}
          </span>
        )}
      </div>
      {editTimes && (
        <div className="flex items-center gap-1 text-[10px]">
          <input
            value={startText}
            onChange={(e) => setStartText(e.target.value)}
            placeholder="dd:ss"
            aria-label="Olay başlangıcı"
            className={`w-14 bg-zinc-800 border rounded px-1 py-0.5 ${
              start === null ? "border-red-700" : "border-zinc-700"
            }`}
          />
          <span className="text-zinc-500">→</span>
          <input
            value={endText}
            onChange={(e) => setEndText(e.target.value)}
            placeholder="dd:ss"
            aria-label="Olay bitişi"
            className={`w-14 bg-zinc-800 border rounded px-1 py-0.5 ${
              end === null ? "border-red-700" : "border-zinc-700"
            }`}
          />
          {!timesValid && (
            <span className="text-red-300">başlangıç bitişten sonra olamaz</span>
          )}
        </div>
      )}
      <textarea
        value={note}
        onChange={(e) => setNote(e.target.value)}
        maxLength={500}
        rows={2}
        placeholder="Operatör notu (isteğe bağlı)"
        aria-label="Operatör notu"
        className="w-full resize-none rounded border border-zinc-700 bg-zinc-800 px-1.5 py-1 text-[10px] text-zinc-200 placeholder:text-zinc-600"
      />
      <div className="flex items-center gap-1">
        <select
          value={cat}
          onChange={(e) => setCat(e.target.value)}
          className="flex-1 min-w-0 bg-zinc-800 border border-zinc-700 rounded px-1 py-0.5"
          title="Doğrulanan anomalinin kategorisi (model önerisini düzeltebilirsiniz)"
        >
          {categories.map((c) => (
            <option key={c} value={c}>{CATEGORY_TR[c] ?? c}</option>
          ))}
        </select>
        <button
          onClick={confirm}
          disabled={!timesValid}
          className="rounded px-2 py-0.5 bg-emerald-700 hover:bg-emerald-600 text-white disabled:opacity-40"
          title="Gerçek anomali olarak doğrula ve oturum listesine geçir"
        >
          ✔ Anomali
        </button>
        <button
          onClick={() => onDecide({ key: item.key, verdict: "sorun_degil", note })}
          className="rounded px-2 py-0.5 bg-zinc-700 hover:bg-zinc-600"
          title="Yanlış/önemsiz — kuyruktan düş"
        >
          ✘ Değil
        </button>
      </div>
    </div>
  );
}

export default function TriagePanel({
  onSelectFeed, onSeek, feedNames = {}, scopeFeed, title = "Nöbet kuyruğu",
}: {
  onSelectFeed?: (feed: string) => void;
  onSeek?: (feed: string, timestamp: number, video: string) => void;
  feedNames?: Record<string, string>;
  scopeFeed?: string;
  title?: string;
}) {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [reviewer, setReviewer] = useState(
    () => localStorage.getItem(REVIEWER_KEY) ?? "");

  useEffect(() => {
    localStorage.setItem(REVIEWER_KEY, reviewer);
  }, [reviewer]);

  const loadSnapshot = useCallback(async () => {
    const response = await fetch("/api/triage");
    if (!response.ok) throw new Error("İnceleme kayıtları alınamadı.");
    setSnap(await response.json());
  }, []);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const response = await fetch("/api/triage");
        if (!response.ok) throw new Error("İnceleme kayıtları alınamadı.");
        const body = await response.json();
        if (alive) setSnap(body);
      } catch (err) {
        if (alive) setError((err as Error).message);
      }
    };
    poll();
    const id = setInterval(poll, 2500);
    return () => { alive = false; clearInterval(id); };
  }, []);

  const decide = async (d: Decision) => {
    setError("");
    const response = await fetch("/api/triage/decide", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...d, reviewer }),
    });
    if (!response.ok) {
      const body = await response.json();
      setError(body.detail ?? "Operatör kararı kaydedilemedi.");
      return;
    }
    await loadSnapshot();
  };

  const requestAction = async (item: TriageItem, action: string) => {
    setError("");
    const response = await fetch("/api/actions/request", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action,
        incident_id: item.incident_id,
        feed: item.feed,
      }),
    });
    if (!response.ok) {
      const body = await response.json();
      setError(body.detail ?? "Aksiyon taslağı istenemedi.");
      return;
    }
    await loadSnapshot();
  };

  if (!snap) return null;
  const pending = scopeFeed === undefined
    ? snap.pending : snap.pending.filter((item) => item.feed === scopeFeed);
  const confirmed = scopeFeed === undefined
    ? snap.confirmed : snap.confirmed.filter((item) => item.feed === scopeFeed);
  return (
    <div className="w-80 shrink-0 flex flex-col gap-2 min-h-0 text-sm">
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-2 flex-1 min-h-0 flex flex-col">
        <div className="font-bold mb-1.5 flex items-center gap-1">
          <span>⚑ {title}</span>
          {pending.length > 0 && (
            <span className="rounded-full bg-amber-700 text-white px-1.5 text-xs">
              {pending.length}
            </span>
          )}
          <input
            value={reviewer}
            onChange={(e) => setReviewer(e.target.value)}
            placeholder="operatör"
            aria-label="Operatör adı"
            title="Kararları kimin verdiği nöbet defterine yazılır"
            className="ml-auto w-20 bg-zinc-800 border border-zinc-700 rounded px-1 py-0.5 text-[10px] font-normal"
          />
        </div>
        {error && <div className="mb-1.5 text-[10px] text-red-300">{error}</div>}
        <div className="flex-1 min-h-0 overflow-y-auto space-y-1.5">
          {pending.length === 0 && (
            <div className="text-zinc-500 text-xs">Bekleyen olay yok.</div>
          )}
          {pending.map((i) => (
            <div key={i.key} onClick={() => onSelectFeed?.(i.feed)}>
              <PendingCard item={i} categories={snap.categories}
                           feedLabel={feedNames[i.feed] || i.feed || "ana akış"}
                           onDecide={decide} onSeek={onSeek} />
            </div>
          ))}
        </div>
        {snap.rules.length > 0 && (
          <div className="mt-1.5 pt-1.5 border-t border-zinc-800 text-xs space-y-1">
            <div className="text-zinc-500">
              Öğrenilen kurallar · {snap.auto_dismissed} otomatik elendi
            </div>
            {snap.rules.map((r) => (
              <div key={`${r.feed}:${r.category}`}
                   className="flex items-center gap-1 text-zinc-400">
                <span className="truncate">
                  {feedNames[r.feed] || r.feed}: {CATEGORY_TR[r.category] ?? r.category}
                  {" "}olağan ({r.auto_count}× elendi)
                </span>
                <button
                  onClick={async () => {
                    await fetch("/api/triage/rule_sil", {
                      method: "POST",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ feed: r.feed, category: r.category }),
                    });
                  }}
                  title="Kuralı iptal et — bu tespitler yeniden kuyruğa düşer"
                  className="ml-auto shrink-0 text-zinc-600 hover:text-red-400"
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-2 max-h-[45%] flex flex-col">
        <div className="font-bold mb-1.5">
          ✔ Bu oturumda tespit edilenler
          <span className="ml-1 text-zinc-500 text-xs font-normal">
            {confirmed.length} anomali · {snap.dismissed_count} elendi
          </span>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto space-y-1 text-xs">
          {confirmed.length === 0 && (
            <div className="text-zinc-500">Henüz doğrulanan anomali yok.</div>
          )}
          {confirmed.map((i) => (
            <div key={i.key} className="rounded border border-emerald-900/60 bg-emerald-950/20 px-2 py-1 space-y-1">
              <span className="font-medium text-emerald-300">
                {CATEGORY_TR[i.operator_category] ?? i.operator_category}
              </span>
              <span className="text-zinc-400">
                {" "}· {feedNames[i.feed] || i.feed || "ana akış"} · {clock(i.t)}
                {i.decided_wall && ` · ${wallClock(i.decided_wall)}`}
              </span>
              <div className="text-zinc-300 truncate">{i.title}</div>
              {i.note && <div className="text-zinc-500 truncate">{i.note}</div>}
              {(i.suggested_actions ?? []).length > 0 && (
                <div className="border-t border-emerald-900/50 pt-1">
                  <div className="mb-1 text-[9px] uppercase tracking-wide text-zinc-500">
                    Güvenli yerel taslak önerileri
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {(i.suggested_actions ?? []).map((suggestion) => (
                      <button
                        key={suggestion.action}
                        disabled={suggestion.status !== "available"}
                        onClick={() => requestAction(i, suggestion.action)}
                        className="rounded border border-amber-800 px-1.5 py-0.5 text-[9px] text-amber-200 hover:bg-amber-950/50 disabled:border-zinc-800 disabled:text-zinc-600"
                        title="Yalnız operatör onayına gidecek yerel taslak isteği oluşturur"
                      >
                        {suggestion.status === "available"
                          ? `+ ${suggestion.label}`
                          : `${suggestion.label} · ${suggestion.status}`}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
