import { useRef, useState } from "react";

/** Analiz paketini (.zip) içeri alır. Analiz ve Olay inceleme çubuklarında aynı düğme. */
export default function ImportPackage() {
  const input = useRef<HTMLInputElement | null>(null);
  const [note, setNote] = useState("");

  const load = async (file: File) => {
    setNote("içeri alınıyor…");
    try {
      const r = await fetch("/api/runs/import", {
        method: "POST",
        headers: { "Content-Type": "application/zip" },
        body: file,
      });
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail ?? r.statusText);
      setNote(`✔ ${body.run_id}: ${body.verdict}`);
    } catch (err) {
      setNote(`✖ içeri alınamadı: ${(err as Error).message}`);
    }
  };

  return (
    <>
      <input
        ref={input}
        type="file"
        accept=".zip"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) load(file);
          e.target.value = "";
        }}
      />
      <button
        onClick={() => input.current?.click()}
        title="Analiz paketini (.zip) içeri al — sohbet paket üzerinde tam yetenekle çalışır"
        className="btn btn-outline"
      >
        ↑ içeri al
      </button>
      {note && (
        <button
          onClick={() => setNote("")}
          title={`${note} — kapatmak için tıklayın`}
          className="max-w-52 truncate text-xs text-zinc-400 hover:text-zinc-200"
        >
          {note}
        </button>
      )}
    </>
  );
}
