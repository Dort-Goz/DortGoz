import { useEffect, useState } from "react";

interface TriageItem {
  key: string;
  feed: string;
  incident_id: string;
  t: number;
  wall: number;
  title: string;
  model_category: string;
  risk: string;
  phase: string;
  thumbnail: string | null;
  evidence: string | null;
  sample: boolean;
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
}

export interface Decision {
  key: string;
  verdict: string;
  category?: string;
  reviewer?: string;
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

function PendingCard({ item, categories, feedLabel, onDecide }: {
  item: TriageItem;
  categories: string[];
  feedLabel: string;
  onDecide: (d: Decision) => void;
}) {
  const [cat, setCat] = useState(
    categories.includes(item.model_category) ? item.model_category : "bilinmeyen");
  const [editTimes, setEditTimes] = useState(false);
  const [startText, setStartText] = useState(clock(item.model_start ?? item.t));
  const [endText, setEndText] = useState(clock(item.model_end ?? item.t));

  const start = parseClock(startText);
  const end = parseClock(endText);
  const timesValid = !editTimes || (start !== null && end !== null && start <= end);

  const confirm = () =>
    onDecide({
      key: item.key,
      verdict: "anomali",
      category: cat,
      operator_start: editTimes ? start : null,
      operator_end: editTimes ? end : null,
    });

  return (
    <div className="rounded border border-zinc-700 bg-zinc-900/70 p-2 space-y-1.5 text-xs">
      <div className="space-y-1.5">
        {item.evidence ? (
          <video
            src={item.evidence}
            controls
            preload="metadata"
            className="w-full aspect-video rounded bg-black"
            title="kanıt klibi — olay penceresi"
          />
        ) : item.thumbnail ? (
          <img src={item.thumbnail} alt=""
               className="w-full aspect-video object-cover rounded" />
        ) : null}
        <div className="min-w-0">
          <div className="font-medium">{item.title}</div>
          <div className="text-zinc-400 truncate" title={feedLabel}>
            {feedLabel} · video {clock(item.t)} · {wallClock(item.wall)}
          </div>
          <div className="flex flex-wrap gap-1 mt-1">
            {item.sample ? (
              <span className="rounded px-1 whitespace-nowrap bg-sky-900 text-sky-200"
                    title="Rastgele denetim örneği: model bu pencerede olay görmedi. ‘Sorun değil’ = model haklı. Kategori seçmek = model olayı KAÇIRMIŞ.">
                denetim örneği
              </span>
            ) : (
              <span className={`rounded px-1 whitespace-nowrap ${RISK_CLS[item.risk] ?? "bg-zinc-800"}`}>
                {item.risk}
              </span>
            )}
            <span className="rounded px-1 whitespace-nowrap bg-zinc-800 text-zinc-300">
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
      </div>
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
      <div className="space-y-1">
        <select
          value={cat}
          onChange={(e) => setCat(e.target.value)}
          className="w-full bg-zinc-800 border border-zinc-700 rounded px-1 py-0.5"
          title="Doğrulanan anomalinin kategorisi (model önerisini düzeltebilirsiniz)"
        >
          {categories.map((c) => (
            <option key={c} value={c}>{CATEGORY_TR[c] ?? c}</option>
          ))}
        </select>
        <div className="flex gap-1">
          <button
            onClick={confirm}
            disabled={!timesValid}
            className="flex-1 rounded px-2 py-1 bg-emerald-700 hover:bg-emerald-600 text-white disabled:opacity-40"
            title="Gerçek anomali olarak doğrula ve oturum listesine geçir"
          >
            ✔ Anomali
          </button>
          <button
            onClick={() => onDecide({ key: item.key, verdict: "sorun_degil" })}
            className="flex-1 rounded px-2 py-1 bg-zinc-700 hover:bg-zinc-600"
            title="Yanlış/önemsiz — kuyruktan düş"
          >
            ✘ Sorun değil
          </button>
        </div>
      </div>
    </div>
  );
}

export default function TriagePanel({ onSelectFeed, feedNames = {} }: {
  onSelectFeed?: (feed: string) => void;
  feedNames?: Record<string, string>;
}) {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [reviewer, setReviewer] = useState(
    () => localStorage.getItem(REVIEWER_KEY) ?? "");

  useEffect(() => {
    localStorage.setItem(REVIEWER_KEY, reviewer);
  }, [reviewer]);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const r = await fetch("/api/triage");
        const body = await r.json();
        if (alive) setSnap(body);
      } catch {}
    };
    poll();
    const id = setInterval(poll, 2500);
    return () => { alive = false; clearInterval(id); };
  }, []);

  const decide = async (d: Decision) => {
    await fetch("/api/triage/decide", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...d, reviewer }),
    });
    const r = await fetch("/api/triage");
    setSnap(await r.json());
  };

  if (!snap) return null;
  return (
    <div className="w-[22rem] shrink-0 flex flex-col gap-2 min-h-0 text-sm">
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-2 flex-1 min-h-0 flex flex-col">
        <div className="font-bold mb-1.5 flex items-center gap-1">
          <span>⚑ Nöbet kuyruğu</span>
          {snap.pending.length > 0 && (
            <span className="rounded-full bg-amber-700 text-white px-1.5 text-xs">
              {snap.pending.length}
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
        <div className="flex-1 min-h-0 overflow-y-auto space-y-1.5">
          {snap.pending.length === 0 && (
            <div className="text-zinc-500 text-xs">Bekleyen olay yok.</div>
          )}
          {snap.pending.map((i) => (
            <div key={i.key} onClick={() => onSelectFeed?.(i.feed)}>
              <PendingCard item={i} categories={snap.categories}
                           feedLabel={feedNames[i.feed] || i.feed || "ana akış"}
                           onDecide={decide} />
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
            {snap.confirmed.length} anomali · {snap.dismissed_count} elendi
          </span>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto space-y-1 text-xs">
          {snap.confirmed.length === 0 && (
            <div className="text-zinc-500">Henüz doğrulanan anomali yok.</div>
          )}
          {snap.confirmed.map((i) => (
            <div key={i.key} className="rounded border border-emerald-900/60 bg-emerald-950/20 px-2 py-1">
              <span className="font-medium text-emerald-300">
                {CATEGORY_TR[i.operator_category] ?? i.operator_category}
              </span>
              <span className="text-zinc-400">
                {" "}· {feedNames[i.feed] || i.feed || "ana akış"} · {clock(i.t)}
                {i.decided_wall && ` · ${wallClock(i.decided_wall)}`}
              </span>
              <div className="text-zinc-300 truncate">{i.title}</div>
              {i.note && <div className="text-zinc-500 truncate">{i.note}</div>}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
