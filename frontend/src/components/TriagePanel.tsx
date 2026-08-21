import { useCallback, useEffect, useState } from "react";
import type { EventEvidenceRef } from "../types/events";

interface SuggestedAction {
  action: string;
  label: string;
  status: "available" | "pending" | "prepared" | "rejected" | "failed";
  request_id: string | null;
}

/** `/api/triage` kaydı (backend TriageItem aynası). */
interface TriageItem {
  key: string;
  feed: string;
  run_id: string;
  video: string;
  incident_id: string;
  event_id: string | null;
  t: number;
  wall: number;
  title: string;
  model_category: string;
  risk: string;
  phase: string;
  thumbnail: string | null;
  evidence: string | null;
  evidence_refs: EventEvidenceRef[];
  sample: boolean;
  needs_review: boolean;
  review_reason: string;
  verdict: string;
  operator_category: string;
  note: string;
  decided_wall: number | null;
  tekrar: number;
  review_ids: string[];
  operator_risk: string;
  false_alarm_reason: string;
  intervention_required: boolean | null;
  review_start: number | null;
  review_peak: number | null;
  review_end: number | null;
  clip_url: string | null;
  clip_start: number | null;
  clip_end: number | null;
  media_thumbnail_url: string | null;
  intervention_score: number;
  intervention_band: "routine" | "review" | "high" | "urgent";
  intervention_reasons: string[];
  priority_ruleset_version: string;
  event_start: number | null;
  event_peak: number | null;
  event_end: number | null;
  suggested_actions?: SuggestedAction[];
}

type TriageVerdict = "anomali" | "sorun_degil";

interface TriageDecision {
  key: string;
  verdict: TriageVerdict;
  category?: string;
  risk_level?: string;
  start_time?: number;
  peak_time?: number;
  end_time?: number;
  false_alarm_reason?: string;
  intervention_required: boolean;
  note: string;
}

interface RuleProposal {
  proposal_id: string;
  feed: string;
  category: string;
  status: "proposed" | "approved";
  dismissal_count: number;
  reason: string;
  expires_at: string | null;
  auto_applied_count: number;
  revision: number;
}

interface Snapshot {
  pending: TriageItem[];
  confirmed: TriageItem[];
  dismissed_count: number;
  auto_dismissed: number;
  rule_proposals: RuleProposal[];
  categories: string[];
  protected_categories: string[];
  queue_overflow_count: number;
  critical_overflow_count: number;
}

/** Teknik gerekçe metnini operatör diline çevirir (ham metin tooltip'te kalır).
 *  "Runtime evidence yalnız provisional... (event[0]=VALIDATED)" gibi satırlar
 *  mühendis jargonu — operatöre KARARINI etkileyen bilgiyi söyler. */
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

const RISK_TR: Record<string, string> = {
  dusuk: "Düşük",
  orta: "Orta",
  yuksek: "Yüksek",
  kritik: "Kritik",
};

const FALSE_ALARM_TR: Record<string, string> = {
  normal_activity: "Olağan hareket",
  camera_condition: "Kamera veya ışık koşulu",
  occlusion: "Görüş engeli",
  reflection_or_shadow: "Yansıma veya gölge",
  duplicate_event: "Aynı olayın tekrarı",
  wrong_classification: "Yanlış sınıflandırma",
  other: "Diğer",
};

const PRIORITY_TR = {
  routine: "Rutin",
  review: "İncelenmeli",
  high: "Yüksek",
  urgent: "Acil",
} as const;

const PRIORITY_CLS = {
  routine: "bg-zinc-800 text-zinc-300",
  review: "bg-amber-900 text-amber-200",
  high: "bg-orange-900 text-orange-200",
  urgent: "bg-red-900 text-red-100",
} as const;

const clock = (t: number) =>
  `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(Math.floor(t % 60)).padStart(2, "0")}`;

export function parseClock(text: string): number | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const parts = trimmed.split(":");
  if (parts.length > 2) return null;
  const numbers = parts.map((part) => Number(part));
  if (numbers.some((value) => !Number.isFinite(value) || value < 0)) return null;
  return numbers.length === 2 ? numbers[0] * 60 + numbers[1] : numbers[0];
}

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
  onDecide: (decision: TriageDecision) => Promise<boolean>;
  onSeek?: (feed: string, timestamp: number, video: string) => void;
}) {
  const [verdict, setVerdict] = useState<TriageVerdict | "">("");
  const [cat, setCat] = useState(
    categories.includes(item.model_category) ? item.model_category : "bilinmeyen");
  const [risk, setRisk] = useState(item.risk);
  const [start, setStart] = useState(item.event_start ?? item.clip_start ?? item.t);
  const [peak, setPeak] = useState(item.event_peak ?? item.t);
  const [end, setEnd] = useState(item.event_end ?? item.clip_end ?? item.t);
  const [falseAlarmReason, setFalseAlarmReason] = useState("");
  const [intervention, setIntervention] = useState<"" | "yes" | "no">("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  const validTimes = start >= 0 && start <= peak && peak <= end;
  const canSubmit = intervention !== ""
    && (verdict === "anomali"
      ? validTimes
      : Boolean(falseAlarmReason)
        && (falseAlarmReason !== "other" || Boolean(note.trim())));

  // Gönderim düşerse kart KİLİTLENMEMELİ: busy her yolda temizlenir, form
  // açık kalır ve operatör aynı olayı yeniden karara bağlayabilir.
  const submit = async () => {
    if (!verdict || !canSubmit) return;
    setBusy(true);
    setFailed(false);
    try {
      const saved = await onDecide({
        key: item.key,
        verdict,
        ...(verdict === "anomali" ? {
          category: cat,
          risk_level: risk,
          start_time: start,
          peak_time: peak,
          end_time: end,
        } : {
          false_alarm_reason: falseAlarmReason,
        }),
        intervention_required: intervention === "yes",
        note: note.trim(),
      });
      if (saved) setVerdict("");
      else setFailed(true);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };

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
            <span
              className={`rounded px-1 font-medium ${PRIORITY_CLS[item.intervention_band]}`}
              title={item.intervention_reasons.join("\n")}
            >
              {item.intervention_score} · {PRIORITY_TR[item.intervention_band]}
            </span>
            {item.sample ? (
              <span className="rounded bg-sky-900 px-1 text-sky-200">
                denetim örneği
              </span>
            ) : (
              <span className={`rounded px-1 ${RISK_CLS[item.risk] ?? "bg-zinc-800"}`}>
                {item.risk}
              </span>
            )}
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
        {onSeek && item.video && (
          <button
            onClick={() => onSeek(item.feed, item.t, item.video)}
            className="ml-auto shrink-0 rounded border border-sky-800 px-1.5 py-1 text-[10px] text-sky-300 hover:bg-sky-950/50"
            title="Videoyu olay anına götür"
          >
            ▶ videoda aç
          </button>
        )}
      </div>
      {(item.evidence_refs ?? []).length > 0 && (
        <div>
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-zinc-500">
            Doğrulanmış video kanıtı
          </div>
          <div className="grid grid-cols-3 gap-1">
            {selectReviewEvidence(item.evidence_refs, item.event_peak ?? item.t).map((evidence) => (
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
      {item.intervention_reasons.length > 0 && (
        <div className="truncate text-zinc-500" title={item.intervention_reasons.join("\n")}>
          Öncelik: {item.intervention_reasons.join(" · ")}
        </div>
      )}
      {(item.clip_url || item.evidence) && (
        <video
          controls
          preload="metadata"
          poster={item.media_thumbnail_url ?? undefined}
          src={item.clip_url ?? item.evidence ?? undefined}
          className="max-h-40 w-full rounded bg-black object-contain"
        >
          Tarayıcınız olay klibini oynatamıyor.
        </video>
      )}
      {!verdict ? (
        <div className="grid grid-cols-2 gap-1">
          <button
            onClick={() => setVerdict("anomali")}
            className="rounded bg-emerald-700 px-2 py-1 text-white hover:bg-emerald-600"
          >
            ✔ Anomali
          </button>
          <button
            onClick={() => setVerdict("sorun_degil")}
            className="rounded bg-zinc-700 px-2 py-1 hover:bg-zinc-600"
          >
            ✘ Sorun değil
          </button>
        </div>
      ) : (
        <div className="space-y-2 rounded border border-zinc-700 bg-zinc-950/70 p-2">
          <div className="flex items-center justify-between">
            <span className={verdict === "anomali" ? "text-emerald-300" : "text-zinc-300"}>
              {verdict === "anomali" ? "Anomali geri bildirimi" : "Yanlış alarm geri bildirimi"}
            </span>
            <button onClick={() => setVerdict("")} className="text-zinc-500 hover:text-zinc-200">
              Vazgeç ×
            </button>
          </div>

          {verdict === "anomali" ? (
            <>
              <label className="block text-zinc-400">
                Doğru olay türü
                <select
                  value={cat}
                  onChange={(event) => setCat(event.target.value)}
                  className="mt-0.5 w-full rounded border border-zinc-700 bg-zinc-900 px-1.5 py-1 text-zinc-200"
                >
                  {categories.map((category) => (
                    <option key={category} value={category}>
                      {CATEGORY_TR[category] ?? category}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block text-zinc-400">
                Doğru risk seviyesi
                <select
                  value={risk}
                  onChange={(event) => setRisk(event.target.value)}
                  className="mt-0.5 w-full rounded border border-zinc-700 bg-zinc-900 px-1.5 py-1 text-zinc-200"
                >
                  {Object.entries(RISK_TR).map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </select>
              </label>
              <div>
                <div className="mb-0.5 text-zinc-400">Olay zamanı (saniye)</div>
                <div className="grid grid-cols-3 gap-1">
                  {([
                    ["Başlangıç", start, setStart],
                    ["Zirve", peak, setPeak],
                    ["Bitiş", end, setEnd],
                  ] as const).map(([label, value, setter]) => (
                    <label key={label} className="text-[10px] text-zinc-500">
                      {label}
                      <input
                        type="number"
                        min="0"
                        step="0.1"
                        value={value}
                        onChange={(event) => setter(Number(event.target.value))}
                        className="mt-0.5 w-full rounded border border-zinc-700 bg-zinc-900 px-1 py-1 text-right text-xs text-zinc-200"
                      />
                    </label>
                  ))}
                </div>
                {!validTimes && <div className="mt-1 text-red-300">Başlangıç ≤ zirve ≤ bitiş olmalı.</div>}
              </div>
            </>
          ) : (
            <label className="block text-zinc-400">
              Yanlış alarm nedeni
              <select
                value={falseAlarmReason}
                onChange={(event) => setFalseAlarmReason(event.target.value)}
                className="mt-0.5 w-full rounded border border-zinc-700 bg-zinc-900 px-1.5 py-1 text-zinc-200"
              >
                <option value="">Neden seçin</option>
                {Object.entries(FALSE_ALARM_TR).map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
            </label>
          )}

          <label className="block text-zinc-400">
            Müdahale gerekli miydi?
            <select
              value={intervention}
              onChange={(event) => setIntervention(event.target.value as "" | "yes" | "no")}
              className="mt-0.5 w-full rounded border border-zinc-700 bg-zinc-900 px-1.5 py-1 text-zinc-200"
            >
              <option value="">Seçin</option>
              <option value="yes">Evet, gerekliydi</option>
              <option value="no">Hayır, gerekli değildi</option>
            </select>
          </label>
          <label className="block text-zinc-400">
            Operatör notu {falseAlarmReason === "other" ? "(zorunlu)" : "(isteğe bağlı)"}
            <textarea
              value={note}
              onChange={(event) => setNote(event.target.value)}
              maxLength={2000}
              rows={2}
              className="mt-0.5 w-full resize-none rounded border border-zinc-700 bg-zinc-900 px-1.5 py-1 text-zinc-200"
              placeholder="Kararı açıklayan kısa not"
            />
          </label>
          {failed && (
            <div className="rounded border border-red-900 bg-red-950/50 px-1.5 py-1 text-red-200">
              Karar kaydedilemedi. Bağlantıyı denetleyin ve yeniden deneyin.
            </div>
          )}
          <button
            disabled={busy || !canSubmit}
            onClick={submit}
            className="w-full rounded bg-sky-700 px-2 py-1.5 font-medium text-white hover:bg-sky-600 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {busy ? "Kaydediliyor…" : "Kararı kalıcı kaydet"}
          </button>
          <p className="text-[10px] text-zinc-500">
            Bu karar geri bildirimdir. Eğitim izni ayrıca verilir.
          </p>
        </div>
      )}
    </div>
  );
}

export default function TriagePanel({
  onSelectFeed,
  onOpenTraining,
  onSeek,
  feedNames = {},
  scopeFeed,
  title = "Nöbet kuyruğu",
}: {
  onSelectFeed?: (feed: string) => void;
  onOpenTraining?: (eventId: string) => void;
  onSeek?: (feed: string, timestamp: number, video: string) => void;
  /** akış kimliği → insan-okur ad (canlı ızgaradan; yoksa kimlik gösterilir) */
  feedNames?: Record<string, string>;
  scopeFeed?: string;
  title?: string;
}) {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [reviewer, setReviewer] = useState(
    () => localStorage.getItem("dortgoz.reviewer") ?? "operator",
  );

  useEffect(() => {
    localStorage.setItem("dortgoz.reviewer", reviewer);
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
        const r = await fetch("/api/triage");
        const body = await r.json();
        if (alive) setSnap(body);
      } catch { /* geçici kopukluk */ }
    };
    poll();
    const id = setInterval(poll, 2500);
    return () => { alive = false; clearInterval(id); };
  }, []);

  const decide = async (decision: TriageDecision): Promise<boolean> => {
    if (!reviewer.trim()) {
      setError("İnceleyen adı boş olamaz.");
      return false;
    }
    let response: Response;
    try {
      response = await fetch("/api/triage/decide", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...decision, reviewer: reviewer.trim() }),
      });
    } catch {
      // İstek hiç ulaşmadı — kart açık kalır, operatör yeniden dener.
      setError("Karar sunucuya iletilemedi. Bağlantıyı denetleyip yeniden deneyin.");
      return false;
    }
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      setError(body.detail || body.error?.message || "Karar kalıcı kayda yazılamadı.");
      return false;
    }
    setError("");
    // Kuyruğu hemen tazele (sonraki poll'u bekletme). Tazeleme hatası kararı
    // geçersiz kılmaz; sonraki poll kuyruğu düzeltir.
    try {
      const r = await fetch("/api/triage");
      setSnap(await r.json());
    } catch { /* geçici kopukluk */ }
    return true;
  };

  const ruleAction = async (
    proposal: RuleProposal,
    action: "approve" | "reject" | "revoke",
  ) => {
    let response: Response;
    try {
      response = await fetch(`/api/triage/rules/${proposal.proposal_id}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          action === "approve"
            ? {
                duration_hours: 24,
                revision: proposal.revision,
                reviewer: reviewer.trim(),
              }
            : { reviewer: reviewer.trim() },
        ),
      });
    } catch {
      setError("Kural kararı sunucuya iletilemedi. Yeniden deneyin.");
      return;
    }
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      setError(body.detail || "Kural kararı kaydedilemedi.");
      return;
    }
    setError("");
    setSnap(body);
  };

  const requestAction = async (item: TriageItem, action: string) => {
    setError("");
    let response: Response;
    try {
      response = await fetch("/api/actions/request", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action,
          incident_id: item.incident_id,
          feed: item.feed,
        }),
      });
    } catch {
      setError("Aksiyon taslağı sunucuya iletilemedi.");
      return;
    }
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      setError(body.detail || "Aksiyon taslağı istenemedi.");
      return;
    }
    try {
      await loadSnapshot();
    } catch {
      // İstek kaydedildi. Sonraki poll görünümü yeniler.
    }
  };

  if (!snap) return null;
  const pending = scopeFeed === undefined
    ? snap.pending : snap.pending.filter((item) => item.feed === scopeFeed);
  const confirmed = scopeFeed === undefined
    ? snap.confirmed : snap.confirmed.filter((item) => item.feed === scopeFeed);
  return (
    <div className="w-80 shrink-0 flex flex-col gap-2 min-h-0 text-sm">
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-2 flex-1 min-h-0 flex flex-col">
        <div className="font-bold mb-1.5">
          ⚑ {title}
          {pending.length > 0 && (
            <span className="ml-1 rounded-full bg-amber-700 text-white px-1.5 text-xs">
              {pending.length}
            </span>
          )}
        </div>
        <label className="mb-1.5 flex items-center gap-2 text-xs text-zinc-500">
          İnceleyen
          <input
            value={reviewer}
            onChange={(event) => setReviewer(event.target.value)}
            maxLength={120}
            className="min-w-0 flex-1 rounded border border-zinc-700 bg-zinc-950 px-1.5 py-1 text-zinc-300"
          />
        </label>
        {error && (
          <div className="mb-1.5 rounded border border-red-900 bg-red-950/50 px-2 py-1 text-xs text-red-200">
            {error}
          </div>
        )}
        {snap.critical_overflow_count > 0 && (
          <div className="mb-1.5 rounded border border-red-800 bg-red-950/60 px-2 py-1 text-xs text-red-100">
            Kuyruk dolu. {snap.critical_overflow_count} kritik olay güvenlik için kuyrukta tutuluyor.
          </div>
        )}
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
        {/* Üç ret yalnız öneri üretir. Ayrı onay ve süre olmadan kural çalışmaz. */}
        {snap.rule_proposals.length > 0 && (
          <div className="mt-1.5 pt-1.5 border-t border-zinc-800 text-xs space-y-1">
            <div className="text-zinc-500">
              Kontrollü kural önerileri · {snap.auto_dismissed} süreli elendi
            </div>
            {snap.rule_proposals.map((proposal) => (
              <div key={proposal.proposal_id}
                   className="rounded border border-zinc-800 p-1.5 text-zinc-400 space-y-1">
                <div>
                  {feedNames[proposal.feed] || proposal.feed}: {CATEGORY_TR[proposal.category] ?? proposal.category}
                  {" "}· {proposal.dismissal_count} operatör reddi
                </div>
                {proposal.status === "proposed" ? (
                  <div className="flex gap-1">
                    <button
                      onClick={() => ruleAction(proposal, "approve")}
                      className="rounded bg-amber-800 px-1.5 py-0.5 text-amber-100 hover:bg-amber-700"
                      title="Kuralı yalnız 24 saat için etkinleştir"
                    >
                      24 saat onayla
                    </button>
                    <button
                      onClick={() => ruleAction(proposal, "reject")}
                      className="rounded bg-zinc-800 px-1.5 py-0.5 hover:bg-zinc-700"
                    >
                      Reddet
                    </button>
                  </div>
                ) : (
                  <div className="flex items-center gap-1">
                    <span className="text-amber-300">
                      Etkin · {proposal.auto_applied_count}× · {proposal.expires_at
                        ? new Date(proposal.expires_at).toLocaleString("tr-TR")
                        : "süre yok"}
                    </span>
                    <button
                      onClick={() => ruleAction(proposal, "revoke")}
                      className="ml-auto text-zinc-500 hover:text-red-400"
                      title="Kuralı hemen geri al"
                    >
                      Geri al
                    </button>
                  </div>
                )}
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
              <span className={`ml-1 rounded px-1 ${PRIORITY_CLS[i.intervention_band]}`}>
                {i.intervention_score} · {PRIORITY_TR[i.intervention_band]}
              </span>
              <span className="text-zinc-400">
                {" "}· {feedNames[i.feed] || i.feed || "ana akış"} · {clock(i.t)}
                {i.decided_wall && ` · ${wallClock(i.decided_wall)}`}
              </span>
              <div className="text-zinc-300 truncate">{i.title}</div>
              {i.note && <div className="text-zinc-500 truncate">{i.note}</div>}
              <div className="text-zinc-500">
                Risk: {RISK_TR[i.operator_risk || i.risk] ?? (i.operator_risk || i.risk)}
                {i.intervention_required != null
                  ? ` · Müdahale ${i.intervention_required ? "gerekli" : "gerekli değil"}`
                  : ""}
              </div>
              {onOpenTraining && i.event_id && (
                <button
                  onClick={() => onOpenTraining(i.event_id!)}
                  className="mt-1 rounded border border-indigo-800 px-1.5 py-0.5 text-indigo-300 hover:bg-indigo-950/40"
                  title="Sonucu yeniden incele ve ayrı geliştirme izni ver"
                >
                  Geliştirmeye incele
                </button>
              )}
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
