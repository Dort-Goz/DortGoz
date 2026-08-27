import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { EventEvidenceRef } from "../types/events";
import LiveAlerts, { playChime, type LiveAlert } from "./LiveAlerts";
import LiveEventModal from "./LiveEventModal";
import { outranks, shouldChime, unseenAlerts } from "../lib/liveAlerts";
import { categoryLabel } from "../lib/labels";

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
  event_id: string | null;
  t: number;
  wall: number;
  title: string;
  model_category: string;
  risk: string;
  phase: string;
  live: boolean;
  thumbnail: string | null;
  evidence: string | null;
  evidence_refs: EventEvidenceRef[];
  sample: boolean;
  source: string;
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


export function humanizeReason(reason: string): string[] {
  // Gerekçeler " · " ile ayrı gelir ve AYRI cümlelerdir. Bunları boşluk ile
  // birleştirmek "...oturmadı Model emin değil:..." gibi okunmaz bir dizi üretir.
  return reason
    .split(" · ")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      if (/provisional|automatic confirmation|VALIDATED|Runtime evidence/i.test(part))
        return "Sistem kanıtı doğruladı; otomatik onay kapalı — karar sizde.";
      if (/model belirsizlik bildirdi:/i.test(part))
        return part.replace(/model belirsizlik bildirdi:/i, "Model emin değil:");
      return part;
    })
    .map((part) => (/[.!?…]$/.test(part) ? part : `${part}.`));
}

export const CATEGORY_TR: Record<string, string> = {
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

export function PendingCard({
  item, categories, feedLabel, onDecide, onSeek, onOpen, layout = "card",
}: {
  item: TriageItem;
  categories: string[];
  feedLabel: string;
  onDecide: (decision: TriageDecision) => Promise<string>;
  onSeek?: (feed: string, timestamp: number, video: string, live: boolean) => void;
  onOpen?: (item: TriageItem) => void;
  layout?: "card" | "modal";
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
  const [failed, setFailed] = useState("");
  /** Sunucudan gelmeyen kanıt karesi kocaman siyah bir kutu bırakmasın. */
  const [brokenFrames, setBrokenFrames] = useState<string[]>([]);
  const modal = layout === "modal";
  /** Ayrıntı kipi varsa kart yalnız özettir; her şey kipte açılır. */
  const summary = Boolean(onOpen) && !modal;

  const validTimes = start >= 0 && start <= peak && peak <= end;
  const canSubmit = intervention !== ""
    && (verdict === "anomali"
      ? validTimes
      : Boolean(falseAlarmReason)
        && (falseAlarmReason !== "other" || Boolean(note.trim())));
  const blockReason = intervention === ""
    ? "Kaydetmek için “Müdahale gerekli miydi?” seçilmelidir."
    : verdict === "anomali"
      ? "Başlangıç ≤ zirve ≤ bitiş olmalı."
      : falseAlarmReason === ""
        ? "Kaydetmek için yanlış alarm nedeni seçilmelidir."
        : "Açıklama için operatör notu zorunludur.";

  const submit = async () => {
    if (!verdict || !canSubmit) return;
    setBusy(true);
    setFailed("");
    try {
      const problem = await onDecide({
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
      if (problem) setFailed(problem);
      else setVerdict("");
    } catch {
      setFailed("Karar sunucuya iletilemedi. Bağlantıyı denetleyin.");
    } finally {
      setBusy(false);
    }
  };

  const headerBlock = (
      <div className="flex items-center gap-2">
        {item.thumbnail && !modal && (
          <img src={item.thumbnail} alt="" className="h-10 w-14 rounded-sm object-cover" />
        )}
        <div className="min-w-0">
          {/* Kip başlığı zaten başlığı ve kamerayı yazıyor; burada tekrar etmeyiz. */}
          {!modal && (
            <div className="line-clamp-2 font-medium text-zinc-200" title={item.title}>
              {item.title}
            </div>
          )}
          {/* Kip başlığı kamerayı ve duvar saatini zaten yazıyor. */}
          {!modal && (
            <div className="text-zinc-400">
              {feedLabel} · video <span className="font-mono">{clock(item.t)}</span> · <span className="font-mono">{wallClock(item.wall)}</span>
            </div>
          )}
          {(item.event_start ?? item.clip_start) != null
            && (item.event_end ?? item.clip_end) != null && (
            <div className="text-zinc-500">
              olay{" "}
              <span className="font-mono text-sky-300">
                {clock((item.event_start ?? item.clip_start)!)}
                –{clock((item.event_end ?? item.clip_end)!)}
              </span>
              {modal && (
                <> · zirve <span className="font-mono text-zinc-400">{clock(item.t)}</span></>
              )}
            </div>
          )}
          <div className="mt-1 flex flex-wrap gap-1">
            <span
              className={`chip ${PRIORITY_CLS[item.intervention_band]}`}
              title={item.intervention_reasons.join("\n")}
            >
              <span className="font-mono">{item.intervention_score}</span> · {PRIORITY_TR[item.intervention_band]}
            </span>
            {item.sample ? (
              <span className="chip bg-sky-900 text-sky-200">
                denetim örneği
              </span>
            ) : (
              <span className={`chip ${RISK_CLS[item.risk] ?? "bg-zinc-800"}`}>
                {item.risk}
              </span>
            )}
            <span className="chip bg-zinc-800 text-zinc-300">
              model: {CATEGORY_TR[item.model_category] ?? item.model_category}
            </span>
            {item.tekrar > 1 && (
              <span className="chip bg-indigo-900 text-indigo-200"
                    title="Aynı kameradan aynı sınıfta tekrar tespit — tek kartta birleştirildi">
                ×<span className="font-mono">{item.tekrar}</span>
              </span>
            )}
          </div>
        </div>
        {onSeek && item.video && !item.live && !modal && !onOpen && (
          <button
            onClick={() => onSeek(item.feed, item.t, item.video, item.live)}
            className="btn btn-outline-accent ml-auto h-6 shrink-0 px-1.5 text-[10px]"
            title="Videoyu olay anına götür"
          >
            ▶ videoda aç
          </button>
        )}
      </div>
  );

  const evidenceBlock = (item.evidence_refs ?? []).length > 0 && (
        <div>
          <div className="microlabel mb-1">Kanıt karesi</div>
          {/* Sabit genişlikli döşeme: tek kare de kalan boşluğa yayılmaz. */}
          <div className="flex flex-wrap gap-1.5">
            {selectReviewEvidence(item.evidence_refs, item.event_peak ?? item.t).map((evidence) => {
              const frameKey = `${evidence.frame_id}:${evidence.timestamp}`;
              const seekable = Boolean(onSeek);
              return (
                <button
                  key={frameKey}
                  type="button"
                  disabled={!seekable}
                  onClick={() => onSeek?.(item.feed, evidence.timestamp, item.video, item.live)}
                  className={`overflow-hidden rounded-sm border border-zinc-800 bg-zinc-950 text-left transition-colors ${
                    modal ? "w-56" : "w-40"
                  } ${seekable ? "cursor-pointer hover:border-sky-700" : "cursor-default"}`}
                  title={`${clock(evidence.timestamp)} · ${evidence.claim}`}
                >
                  {brokenFrames.includes(frameKey) ? (
                    <div className="flex aspect-video w-full items-center justify-center border-b border-dashed border-zinc-800 bg-zinc-900 px-1 text-center text-[9px] text-zinc-600">
                      kanıt karesi alınamadı
                    </div>
                  ) : (
                    <img
                      src={evidenceFrameUrl(item.key, evidence.timestamp)}
                      alt=""
                      loading="lazy"
                      onError={() => setBrokenFrames(
                        (current) => current.includes(frameKey) ? current : [...current, frameKey],
                      )}
                      className="aspect-video w-full bg-black object-cover"
                    />
                  )}
                  <div className="px-1 py-0.5">
                    <div className="font-mono text-[9px] text-sky-300">
                      {clock(evidence.timestamp)}
                    </div>
                    <div className="line-clamp-2 text-[9px] leading-tight text-zinc-400">
                      {evidence.claim}
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        </div>
  );

  const reasonBlock = item.needs_review && item.review_reason && (
        <div className="text-amber-300" title={item.review_reason}>
          <div className="microlabel text-amber-400">İnceleme gerekçesi</div>
          <ul className="list-disc space-y-0.5 pl-4">
            {humanizeReason(item.review_reason).map((part) => (
              <li key={part}>{part}</li>
            ))}
          </ul>
        </div>
  );

  const mediaBlock = (item.clip_url || item.evidence) ? (
        <div className="space-y-1">
          <video
            controls
            autoPlay={modal}
            muted={modal}
            playsInline
            preload="metadata"
            poster={item.media_thumbnail_url ?? undefined}
            src={item.clip_url ?? item.evidence ?? undefined}
            className={modal
              ? "mx-auto max-h-[46vh] w-full rounded-sm bg-black object-contain"
              : "mx-auto max-h-56 w-full max-w-lg rounded-sm bg-black object-contain"}
          >
            Tarayıcınız olay klibini oynatamıyor.
          </video>
          {modal && (
            <div className="flex items-center gap-2 text-[10px] text-zinc-500">
              {item.clip_start != null && item.clip_end != null && (
                <span className="font-mono">
                  kayıt {clock(item.clip_start)}–{clock(item.clip_end)}
                </span>
              )}
              <span className="flex-1" />
              <a
                href={item.clip_url ?? item.evidence ?? "#"}
                download
                title="Kaydı indir — analiz istenirse “Video yükle” ile ayrıca yüklenir"
                className="btn btn-outline h-6 px-1.5 text-[10px]"
              >
                ⇩ kaydı indir
              </a>
            </div>
          )}
        </div>
      ) : modal && (
        <div className="flex h-40 items-center justify-center rounded-sm border border-dashed border-zinc-800 bg-black text-xs text-zinc-500">
          Olay kaydı henüz hazır değil — segment kapanınca kesilir.
        </div>
  );

  const decisionBlock = !verdict ? (
        <div className={`flex gap-1 ${modal ? "mx-auto max-w-md" : ""}`}>
          <button
            onClick={() => setVerdict("anomali")}
            className="btn btn-primary flex-1"
          >
            ✔ Anomali
          </button>
          <button
            onClick={() => setVerdict("sorun_degil")}
            className="btn btn-outline flex-1"
          >
            ✘ Sorun değil
          </button>
        </div>
      ) : (
        <div className="space-y-2 rounded-md border border-zinc-800 bg-zinc-900 p-2">
          <div className="flex items-center justify-between">
            <span className={`font-medium ${verdict === "anomali" ? "text-emerald-300" : "text-zinc-300"}`}>
              {verdict === "anomali" ? "Anomali geri bildirimi" : "Yanlış alarm geri bildirimi"}
            </span>
            <button onClick={() => setVerdict("")} className="btn btn-ghost h-6 px-2">
              Vazgeç ×
            </button>
          </div>

          {verdict === "anomali" ? (
            <>
              <label className="block space-y-1">
                <span className="microlabel block">Doğru olay türü</span>
                <select
                  value={cat}
                  onChange={(event) => setCat(event.target.value)}
                  className="field w-full"
                >
                  {categories.map((category) => (
                    <option key={category} value={category}>
                      {CATEGORY_TR[category] ?? category}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block space-y-1">
                <span className="microlabel block">Doğru risk seviyesi</span>
                <select
                  value={risk}
                  onChange={(event) => setRisk(event.target.value)}
                  className="field w-full"
                >
                  {Object.entries(RISK_TR).map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </select>
              </label>
              <div>
                <div className="microlabel mb-1">Olay zamanı (saniye)</div>
                <div className="grid grid-cols-3 gap-1">
                  {([
                    ["Başlangıç", start, setStart],
                    ["Zirve", peak, setPeak],
                    ["Bitiş", end, setEnd],
                  ] as const).map(([label, value, setter]) => (
                    <label key={label} className="block space-y-1">
                      <span className="microlabel block">{label}</span>
                      <input
                        type="number"
                        min="0"
                        step="0.1"
                        value={value}
                        onChange={(event) => setter(Number(event.target.value))}
                        className="field w-full text-right"
                      />
                    </label>
                  ))}
                </div>
                {!validTimes && <div className="mt-1 text-red-300">Başlangıç ≤ zirve ≤ bitiş olmalı.</div>}
              </div>
            </>
          ) : (
            <label className="block space-y-1">
              <span className="microlabel block">Yanlış alarm nedeni</span>
              <select
                value={falseAlarmReason}
                onChange={(event) => setFalseAlarmReason(event.target.value)}
                className="field w-full"
              >
                <option value="">Neden seçin</option>
                {Object.entries(FALSE_ALARM_TR).map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
            </label>
          )}

          <label className="block space-y-1">
            <span className="microlabel block">Müdahale gerekli miydi?</span>
            <select
              value={intervention}
              onChange={(event) => setIntervention(event.target.value as "" | "yes" | "no")}
              className="field w-full"
            >
              <option value="">Seçin</option>
              <option value="yes">Evet, gerekliydi</option>
              <option value="no">Hayır, gerekli değildi</option>
            </select>
          </label>
          <label className="block space-y-1">
            <span className="microlabel block">
              Operatör notu {falseAlarmReason === "other" ? "(zorunlu)" : "(isteğe bağlı)"}
            </span>
            <textarea
              value={note}
              onChange={(event) => setNote(event.target.value)}
              maxLength={2000}
              rows={2}
              className="field-area resize-none"
              placeholder="Kararı açıklayan kısa not"
            />
          </label>
          {failed && (
            <div className="rounded-sm border border-red-900 bg-red-950/40 px-2 py-1 text-xs text-red-200">
              Karar kaydedilemedi — {failed}
            </div>
          )}
          <button
            disabled={busy || !canSubmit}
            onClick={submit}
            className="btn btn-accent w-full"
          >
            {busy ? "Kaydediliyor…" : "Kararı kalıcı kaydet"}
          </button>
          <p className="text-[10px] text-zinc-500">
            {canSubmit
              ? "Bu karar geri bildirimdir. Eğitim izni ayrıca verilir."
              : blockReason}
          </p>
        </div>
  );

  // Kip geniştir: kayıt solda büyük durur, künye ve kanıt sağ sütunda toplanır.
  if (modal) {
    return (
      <div className="space-y-2 text-xs">
        <div className="grid gap-3 lg:grid-cols-[minmax(0,3fr)_minmax(17rem,2fr)]">
          <div className="space-y-2">{mediaBlock}</div>
          <div className="space-y-2">
            {headerBlock}
            {evidenceBlock}
            {reasonBlock}
          </div>
        </div>
        {decisionBlock}
      </div>
    );
  }

  // Kenar çubuğu özeti: ayrıntı kipte açılır, kartın tamamı tıklanır.
  if (summary) {
    return (
      <div
        role="button"
        tabIndex={0}
        onClick={() => onOpen?.(item)}
        onKeyDown={(pressed) => {
          if (pressed.key !== "Enter" && pressed.key !== " ") return;
          pressed.preventDefault();
          onOpen?.(item);
        }}
        title="Olay klibini ve raporunu büyük ekranda aç"
        className="relative cursor-pointer space-y-1.5 rounded-md border border-zinc-800 bg-zinc-950 p-2 pb-4 text-xs transition-colors hover:border-sky-700 hover:bg-zinc-900"
      >
        {headerBlock}
        <span aria-hidden className="absolute bottom-1 right-1.5 text-[11px] leading-none text-zinc-600">
          ↗
        </span>
      </div>
    );
  }

  return (
    <div className="space-y-1.5 rounded-md border border-zinc-800 bg-zinc-950 p-2 text-xs">
      {headerBlock}
      {evidenceBlock}
      {reasonBlock}
      {mediaBlock}
      {decisionBlock}
    </div>
  );
}

export default function TriagePanel({
  user,
  onSelectFeed,
  onOpenTraining,
  onSeek,
  feedNames = {},
  scopeFeed,
  scopeLive,
  title = "Nöbet kuyruğu",
  layout = "sidebar",
}: {
  /** Konsolun tek kimliği; üst çubuktan gelir ve kararı imzalar. */
  user: string;
  onSelectFeed?: (feed: string) => void;
  onOpenTraining?: (eventId: string) => void;
  onSeek?: (feed: string, timestamp: number, video: string, live: boolean) => void;

  feedNames?: Record<string, string>;
  scopeFeed?: string;
  scopeLive?: boolean;
  title?: string;
  layout?: "sidebar" | "workspace";
}) {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const reviewer = user;
  const [openKey, setOpenKey] = useState("");
  const [dismissed, setDismissed] = useState<string[]>([]);
  const [muted, setMuted] = useState(
    () => localStorage.getItem("dortgoz.uyariSesi") === "kapali",
  );
  const seenRef = useRef<Set<string> | null>(null);
  const chimedRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    localStorage.setItem("dortgoz.uyariSesi", muted ? "kapali" : "acik");
  }, [muted]);

  const loadSnapshot = useCallback(async () => {
    const response = await fetch("/api/triage");
    if (!response.ok) throw new Error("İnceleme kayıtları alınamadı.");
    setSnap(await response.json());
    setError("");
  }, []);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const r = await fetch("/api/triage");
        if (!r.ok) throw new Error();
        const body = await r.json();
        if (alive) {
          setSnap(body);
          setError("");
        }
      } catch {
        if (alive) setError("İnceleme kayıtları alınamadı. Bağlantıyı denetleyin.");
      }
    };
    poll();
    const id = setInterval(poll, 2500);
    return () => { alive = false; clearInterval(id); };
  }, []);

  const decide = async (decision: TriageDecision): Promise<string> => {
    if (!reviewer.trim()) return "İnceleyen adı boş olamaz.";
    let response: Response;
    try {
      response = await fetch("/api/triage/decide", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...decision, reviewer: reviewer.trim() }),
      });
    } catch {
      return "sunucuya ulaşılamadı. Bağlantıyı denetleyip yeniden deneyin.";
    }
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      return body.error?.message || body.detail || "kalıcı kayda yazılamadı.";
    }
    setError("");
    try {
      const r = await fetch("/api/triage");
      setSnap(await r.json());
    } catch {  }
    return "";
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
    }
  };

  const inScope = useCallback(
    (item: TriageItem) =>
      (scopeFeed === undefined || item.feed === scopeFeed)
      && (scopeLive === undefined || item.live === scopeLive),
    [scopeFeed, scopeLive],
  );
  const scopedPending = useMemo(
    () => (snap?.pending ?? []).filter(inScope),
    [snap, inScope],
  );
  const openItem = scopedPending.find((item) => item.key === openKey);

  useEffect(() => {
    if (snap && seenRef.current === null) {
      seenRef.current = new Set(scopedPending.map((item) => item.key));
    }
  }, [snap, scopedPending]);

  useEffect(() => {
    if (openKey && snap && !openItem) setOpenKey("");
  }, [openKey, snap, openItem]);

  const alerts: LiveAlert[] = useMemo(() => {
    if (!scopeLive || seenRef.current === null) return [];
    const seen = new Set([...seenRef.current, ...dismissed, openKey]);
    return unseenAlerts(scopedPending, seen).map((item) => ({
      key: item.key,
      risk: item.risk,
      intervention_band: item.intervention_band,
      intervention_score: item.intervention_score,
      wall: item.wall,
      feedLabel: feedNames[item.feed] || item.feed || "ana akış",
      categoryLabel: categoryLabel(item.model_category),
      outranksWatched: openItem ? outranks(item, openItem) : false,
    }));
  }, [scopeLive, scopedPending, dismissed, openKey, openItem, feedNames]);

  useEffect(() => {
    const fresh = alerts.filter((alert) => !chimedRef.current.has(alert.key));
    if (fresh.length === 0) return;
    for (const alert of fresh) chimedRef.current.add(alert.key);
    if (!muted && shouldChime(fresh)) playChime();
  }, [alerts, muted]);

  const openEvent = useCallback((key: string) => {
    setDismissed((current) => [...current, key]);
    setOpenKey(key);
  }, []);

  const rootClass = layout === "workspace"
    ? "h-full min-h-0 grid grid-cols-[minmax(0,2fr)_minmax(20rem,1fr)] gap-1.5 text-sm"
    : "w-80 shrink-0 flex flex-col gap-1.5 min-h-0 text-sm";
  if (!snap) {
    return (
      <div className={rootClass}>
        <div className="rounded-md border border-red-900 bg-red-950/40 p-3 text-sm text-red-200">
          {error || "İnceleme kayıtları yükleniyor…"}
        </div>
      </div>
    );
  }
  const pending = scopedPending;
  const confirmed = snap.confirmed.filter(inScope);
  const alertStack = (
    <LiveAlerts
      alerts={alerts}
      muted={muted}
      onMuteToggle={() => setMuted((current) => !current)}
      onOpen={openEvent}
      onDismiss={(key) => setDismissed((current) => [...current, key])}
    />
  );
  return (
    <div className={rootClass}>
      <div className="panel flex-1">
        <div className="panel-title">
          <span className="flex-1 truncate">⚑ {title}</span>
          {pending.length > 0 && (
            <span className="chip border border-amber-900 bg-amber-950/40 font-mono text-amber-300">
              {pending.length}
            </span>
          )}
        </div>
        <div className="flex shrink-0 flex-col gap-1.5 p-2 pb-0 empty:hidden">
          {error && (
            <div className="rounded-sm border border-red-900 bg-red-950/40 px-2 py-1 text-xs text-red-200">
              {error}
            </div>
          )}
          {snap.critical_overflow_count > 0 && (
            <div className="rounded-sm border border-red-800 bg-red-950/40 px-2 py-1 text-xs text-red-100">
              Kuyruk dolu. {snap.critical_overflow_count} kritik olay güvenlik için kuyrukta tutuluyor.
            </div>
          )}
        </div>
        <div className="panel-body space-y-1.5 p-1.5">
          {pending.length === 0 && (
            <div className="text-xs text-zinc-500">Bekleyen olay yok.</div>
          )}
          {pending.map((i) => (
            <div key={i.key} onClick={() => onSelectFeed?.(i.feed)}>
              <PendingCard item={i} categories={snap.categories}
                           feedLabel={feedNames[i.feed] || i.feed || "ana akış"}
                           onDecide={decide} onSeek={onSeek}
                           onOpen={scopeLive ? (opened) => openEvent(opened.key) : undefined} />
            </div>
          ))}
        </div>
        {}
        {snap.rule_proposals.length > 0 && (
          <div className="shrink-0 space-y-1.5 border-t border-zinc-800 p-2 text-xs">
            <div className="text-zinc-500">
              Kontrollü kural önerileri · {snap.auto_dismissed} süreli elendi
            </div>
            {snap.rule_proposals.map((proposal) => (
              <div key={proposal.proposal_id}
                   className={`space-y-1 rounded-md p-1.5 text-zinc-400 ${
                     proposal.status === "approved"
                       ? "border-l-2 border-amber-700 bg-zinc-950"
                       : "border border-zinc-800 bg-zinc-950"
                   }`}>
                <div>
                  {feedNames[proposal.feed] || proposal.feed}: {CATEGORY_TR[proposal.category] ?? proposal.category}
                  {" "}· {proposal.dismissal_count} operatör reddi
                </div>
                {proposal.status === "proposed" ? (
                  <div className="flex gap-1">
                    <button
                      onClick={() => ruleAction(proposal, "approve")}
                      className="btn btn-outline-warn h-6"
                      title="Kuralı yalnız 24 saat için etkinleştir"
                    >
                      24 saat onayla
                    </button>
                    <button
                      onClick={() => ruleAction(proposal, "reject")}
                      className="btn btn-outline h-6"
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
      <div className={`panel ${layout === "sidebar" ? "max-h-[45%]" : ""}`}>
        <div className="panel-title">
          <span className="flex-1 truncate" title="Bu oturumda tespit edilenler">✔ Tespit edilenler</span>
          <span className="chip border border-zinc-800 text-zinc-400">
            <span className="font-mono">{confirmed.length}</span> anomali · <span className="font-mono">{snap.dismissed_count}</span> elendi
          </span>
        </div>
        <div className="panel-body space-y-1 p-1.5 text-xs">
          {confirmed.length === 0 && (
            <div className="text-zinc-500">Henüz doğrulanan anomali yok.</div>
          )}
          {confirmed.map((i) => (
            <div key={i.key} className="space-y-1 rounded-sm border-l-2 border-emerald-700 bg-zinc-950 px-2 py-1.5">
              <span className="font-medium text-emerald-300">
                {CATEGORY_TR[i.operator_category] ?? i.operator_category}
              </span>
              <span className={`chip ml-1 ${PRIORITY_CLS[i.intervention_band]}`}>
                <span className="font-mono">{i.intervention_score}</span> · {PRIORITY_TR[i.intervention_band]}
              </span>
              {i.source === "operator" && (
                <span
                  className="chip ml-1 border border-sky-900 bg-sky-950/40 text-sky-300"
                  title="Sistemin kaçırdığı, operatörün gözle bildirdiği olay"
                >
                  ⚑ operatör
                </span>
              )}
              <span className="text-zinc-400">
                {" "}· {feedNames[i.feed] || i.feed || "ana akış"}
                {!(i.live && i.source === "operator") && (
                  <> · <span className="font-mono">{clock(i.t)}</span></>
                )}
                {i.decided_wall && <span className="font-mono"> · {wallClock(i.decided_wall)}</span>}
              </span>
              <div className="truncate text-zinc-300">{i.title}</div>
              {i.note && <div className="truncate text-zinc-500">{i.note}</div>}
              <div className="text-zinc-500">
                Risk: {RISK_TR[i.operator_risk || i.risk] ?? (i.operator_risk || i.risk)}
                {i.intervention_required != null
                  ? ` · Müdahale ${i.intervention_required ? "gerekli" : "gerekli değil"}`
                  : ""}
              </div>
              {onOpenTraining && i.event_id && (
                <button
                  onClick={() => onOpenTraining(i.event_id!)}
                  className="btn btn-outline-accent mt-1 h-6"
                  title="Sonucu yeniden incele ve ayrı geliştirme izni ver"
                >
                  Ayrıntılı incele
                </button>
              )}
              {(i.suggested_actions ?? []).length > 0 && (
                <div className="border-t border-zinc-800 pt-1">
                  <div className="microlabel mb-1">
                    Güvenli yerel taslak önerileri
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {(i.suggested_actions ?? []).map((suggestion) => (
                      <button
                        key={suggestion.action}
                        disabled={suggestion.status !== "available"}
                        onClick={() => requestAction(i, suggestion.action)}
                        className="btn btn-outline-warn h-6 px-1.5 text-[10px] disabled:border-zinc-800 disabled:text-zinc-600"
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
      {openItem ? (
        <LiveEventModal
          title={openItem.title}
          subtitle={`${feedNames[openItem.feed] || openItem.feed || "ana akış"} · ${
            CATEGORY_TR[openItem.model_category] ?? openItem.model_category
          } · ${wallClock(openItem.wall)}`}
          onClose={() => setOpenKey("")}
          alerts={alertStack}
        >
          <PendingCard
            item={openItem}
            categories={snap.categories}
            feedLabel={feedNames[openItem.feed] || openItem.feed || "ana akış"}
            onDecide={decide}
            layout="modal"
          />
        </LiveEventModal>
      ) : alertStack}
    </div>
  );
}
