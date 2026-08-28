import { useEffect, useState } from "react";
import { clock } from "../lib/labels";
import { CATEGORY_TR, parseClock } from "./TriagePanel";

const RISK_TR: Record<string, string> = {
  dusuk: "Düşük", orta: "Orta", yuksek: "Yüksek", kritik: "Kritik",
};

const AGO_OPTIONS = [
  [0, "şimdi gördüm"], [30, "30 sn önce"], [60, "1 dk önce"], [120, "2 dk önce"],
] as const;

const SPAN_OPTIONS = [
  [15, "~15 sn"], [30, "~30 sn"], [60, "~1 dk"], [120, "~2 dk"],
] as const;

export default function OperatorReportDialog({
  live, feeds, initialFeed, runId, video, initialT, user, onClose,
}: {
  live: boolean;
  feeds: Record<string, string>;
  initialFeed: string;
  runId?: string;
  video?: string;
  initialT?: number;
  user: string;
  onClose: () => void;
}) {
  const [feed, setFeed] = useState(initialFeed);
  const [category, setCategory] = useState("bilinmeyen");
  const [risk, setRisk] = useState("orta");
  const [note, setNote] = useState("");
  const [ago, setAgo] = useState(0);
  const [span, setSpan] = useState(30);
  const [startText, setStartText] = useState(clock(Math.max(0, (initialT ?? 0) - 5)));
  const [endText, setEndText] = useState(clock((initialT ?? 0) + 5));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [onClose]);

  const submit = async () => {
    if (note.trim().length < 3) {
      setError("Ne gördüğünüzü kısaca yazın — bildirim gözlem notuyla kayda geçer.");
      return;
    }
    let start: number, end: number;
    if (live) {
      const now = Date.now() / 1000;
      end = now - ago;
      start = end - span;
    } else {
      const s = parseClock(startText);
      const e = parseClock(endText);
      if (s == null || e == null || e <= s) {
        setError("Zaman penceresi geçersiz — mm:ss biçiminde, bitiş başlangıçtan sonra.");
        return;
      }
      start = s;
      end = e;
    }
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/triage/report", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          feed, live, category, risk,
          note: note.trim(),
          reviewer: user.trim() || "operator",
          start, end,
          run_id: live ? "" : (runId ?? ""),
          video: live ? "" : (video ?? ""),
        }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        setError(body.error?.message || body.detail || "Bildirim kaydedilemedi. Yeniden deneyin.");
        setBusy(false);
        return;
      }
      setDone(true);
      setTimeout(onClose, 1300);
    } catch {
      setError("Sunucuya ulaşılamadı. Bağlantıyı denetleyip yeniden deneyin.");
      setBusy(false);
    }
  };

  return (
    <>
      <div
        role="presentation"
        onClick={onClose}
        className="fixed inset-0 z-50 bg-black/80 backdrop-blur-[1px]"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Kaçan olayı bildir"
        className="fixed inset-x-0 top-24 z-50 mx-auto w-[min(26rem,94vw)] overflow-hidden rounded-md border border-zinc-700 bg-zinc-950 shadow-2xl"
      >
        <div className="flex h-10 shrink-0 items-center gap-2 border-b border-zinc-800 bg-zinc-900 px-3">
          <span className="text-sm font-bold text-zinc-100">⚑ Kaçan olayı bildir</span>
          <span className="microlabel truncate">
            {live ? "canlı akış" : "video analizi"}
          </span>
          <span className="flex-1" />
          <button onClick={onClose} className="btn btn-ghost h-7 px-2" title="Kapat (Esc)">
            kapat ✕
          </button>
        </div>

        <div className="space-y-2 p-3 text-xs">
          <p className="text-zinc-500">
            Sistem bu olayı bildirmedi; sizin gördüğünüz kayda geçer ve doğrulanmış
            anomali olarak nöbet defterine yazılır.
          </p>

          {live && (
            <label className="block">
              <span className="microlabel mb-0.5 block">kamera</span>
              <select
                value={feed}
                onChange={(e) => setFeed(e.target.value)}
                className="field w-full"
              >
                {Object.entries(feeds).map(([name, label]) => (
                  <option key={name} value={name}>{label}</option>
                ))}
              </select>
            </label>
          )}

          <div className="grid grid-cols-2 gap-2">
            <label className="block">
              <span className="microlabel mb-0.5 block">olay türü</span>
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                className="field w-full"
              >
                {Object.entries(CATEGORY_TR).map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="microlabel mb-0.5 block">risk</span>
              <select
                value={risk}
                onChange={(e) => setRisk(e.target.value)}
                className="field w-full"
              >
                {Object.entries(RISK_TR).map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
            </label>
          </div>

          {live ? (
            <div className="grid grid-cols-2 gap-2">
              <label className="block">
                <span className="microlabel mb-0.5 block">ne zaman</span>
                <select
                  value={ago}
                  onChange={(e) => setAgo(Number(e.target.value))}
                  className="field w-full"
                >
                  {AGO_OPTIONS.map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className="microlabel mb-0.5 block">süre</span>
                <select
                  value={span}
                  onChange={(e) => setSpan(Number(e.target.value))}
                  className="field w-full"
                >
                  {SPAN_OPTIONS.map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </select>
              </label>
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-2">
              <label className="block">
                <span className="microlabel mb-0.5 block">başlangıç (dd:ss)</span>
                <input
                  value={startText}
                  onChange={(e) => setStartText(e.target.value)}
                  className="field w-full font-mono"
                />
              </label>
              <label className="block">
                <span className="microlabel mb-0.5 block">bitiş (dd:ss)</span>
                <input
                  value={endText}
                  onChange={(e) => setEndText(e.target.value)}
                  className="field w-full font-mono"
                />
              </label>
            </div>
          )}

          <label className="block">
            <span className="microlabel mb-0.5 block">gözlem</span>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={3}
              maxLength={500}
              placeholder="Ne gördünüz? (karar iziyle birlikte kalıcı kayda geçer)"
              className="field-area w-full"
              autoFocus
            />
          </label>

          {error && (
            <div className="rounded-sm border border-red-900 bg-red-950/40 px-2 py-1 text-red-200">
              {error}
            </div>
          )}
          {done ? (
            <div className="rounded-sm border border-emerald-900 bg-emerald-950/40 px-2 py-1 text-emerald-300">
              ✔ Kayda geçti — nöbet defterine yazıldı, “Olay inceleme” sekmesinde açılır.
            </div>
          ) : (
            <div className="flex items-center justify-end gap-1.5">
              <button onClick={onClose} className="btn btn-outline">Vazgeç</button>
              <button
                onClick={submit}
                disabled={busy}
                className="btn btn-accent"
                title="Bildirimi doğrulanmış anomali olarak kaydet"
              >
                {busy ? "kaydediliyor…" : "⚑ Bildir"}
              </button>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
