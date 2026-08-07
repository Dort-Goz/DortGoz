import { useRef, useState } from "react";
import { uploadVideo } from "../lib/api";
import type { VideoMetadata } from "../types/domain";

export default function UploadPanel({ onUploaded }: { onUploaded: (video: VideoMetadata) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const choose = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true); setError(null);
    try { onUploaded(await uploadVideo(file)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Yükleme başarısız."); }
    finally { setBusy(false); if (input.current) input.current.value = ""; }
  };
  return <div className="flex items-center gap-2">
    <input ref={input} className="hidden" type="file" accept="video/*" onChange={(e) => choose(e.target.files?.[0])} />
    <button onClick={() => input.current?.click()} disabled={busy} className="rounded px-2 py-1 border border-zinc-700 text-xs hover:border-zinc-500 disabled:opacity-50">
      {busy ? "Yükleniyor…" : "Video yükle"}
    </button>
    {error && <span className="text-[10px] text-red-300 max-w-52 truncate" title={error}>{error}</span>}
  </div>;
}
