import { useEffect, useState } from "react";

/** `/api/triage` kaydı (backend TriageItem aynası). */
interface TriageItem {
  key: string;
  feed: string;
  incident_id: string;
  event_id: string | null;
  t: number;
  wall: number;
  title: string;
  model_category: string;
  risk: string;
  phase: string;
  thumbnail: string | null;
  needs_review: boolean;
  review_reason: string;
  verdict: string;
  operator_category: string;
  note: string;
  decided_wall: number | null;
  tekrar: number;
  review_ids: string[];
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
}

interface Snapshot {
  pending: TriageItem[];
  confirmed: TriageItem[];
  dismissed_count: number;
  auto_dismissed: number;
  rule_proposals: RuleProposal[];
  categories: string[];
  protected_categories: string[];
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

const clock = (t: number) =>
  `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
const wallClock = (epoch: number) =>
  new Date(epoch * 1000).toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit" });

function PendingCard({ item, categories, feedLabel, onDecide }: {
  item: TriageItem;
  categories: string[];
  feedLabel: string;
  onDecide: (key: string, verdict: string, category?: string) => void;
}) {
  // Kategori varsayılanı modelin önerisi — operatör düzeltebilir
  const [cat, setCat] = useState(
    categories.includes(item.model_category) ? item.model_category : "bilinmeyen");
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
      </div>
      {item.needs_review && item.review_reason && (
        <div className="text-amber-300/90" title={item.review_reason}>
          ? {humanizeReason(item.review_reason)}
        </div>
      )}
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
          onClick={() => onDecide(item.key, "anomali", cat)}
          className="rounded px-2 py-0.5 bg-emerald-700 hover:bg-emerald-600 text-white"
          title="Gerçek anomali olarak doğrula ve oturum listesine geçir"
        >
          ✔ Anomali
        </button>
        <button
          onClick={() => onDecide(item.key, "sorun_degil")}
          className="rounded px-2 py-0.5 bg-zinc-700 hover:bg-zinc-600"
          title="Yanlış/önemsiz — kuyruktan düş"
        >
          ✘ Değil
        </button>
      </div>
    </div>
  );
}

export default function TriagePanel({ onSelectFeed, feedNames = {} }: {
  onSelectFeed?: (feed: string) => void;
  /** akış kimliği → insan-okur ad (canlı ızgaradan; yoksa kimlik gösterilir) */
  feedNames?: Record<string, string>;
}) {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");

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

  const decide = async (key: string, verdict: string, category?: string) => {
    const response = await fetch("/api/triage/decide", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key, verdict, category }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      setError(body.detail || "Karar kalıcı kayda yazılamadı.");
      return;
    }
    setError("");
    // Kuyruğu hemen tazele (sonraki poll'u bekletme)
    const r = await fetch("/api/triage");
    setSnap(await r.json());
  };

  const ruleAction = async (
    proposalId: string,
    action: "approve" | "reject" | "revoke",
  ) => {
    const response = await fetch(`/api/triage/rules/${proposalId}/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(action === "approve" ? { duration_hours: 24 } : {}),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      setError(body.detail || "Kural kararı kaydedilemedi.");
      return;
    }
    setError("");
    setSnap(body);
  };

  if (!snap) return null;
  return (
    <div className="w-72 shrink-0 flex flex-col gap-2 min-h-0 text-sm">
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-2 flex-1 min-h-0 flex flex-col">
        <div className="font-bold mb-1.5">
          ⚑ Nöbet kuyruğu
          {snap.pending.length > 0 && (
            <span className="ml-1 rounded-full bg-amber-700 text-white px-1.5 text-xs">
              {snap.pending.length}
            </span>
          )}
        </div>
        {error && (
          <div className="mb-1.5 rounded border border-red-900 bg-red-950/50 px-2 py-1 text-xs text-red-200">
            {error}
          </div>
        )}
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
                      onClick={() => ruleAction(proposal.proposal_id, "approve")}
                      className="rounded bg-amber-800 px-1.5 py-0.5 text-amber-100 hover:bg-amber-700"
                      title="Kuralı yalnız 24 saat için etkinleştir"
                    >
                      24 saat onayla
                    </button>
                    <button
                      onClick={() => ruleAction(proposal.proposal_id, "reject")}
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
                      onClick={() => ruleAction(proposal.proposal_id, "revoke")}
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
