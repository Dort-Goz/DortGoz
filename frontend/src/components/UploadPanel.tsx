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
  return <div className="flex shrink-0 items-center gap-1.5">
    <input ref={input} className="hidden" type="file" accept="video/*" onChange={(e) => choose(e.target.files?.[0])} />
    <button onClick={() => input.current?.click()} disabled={busy} className="btn btn-outline">
      {busy ? "Yükleniyor…" : "Video yükle"}
    </button>
    {error && <span className="max-w-40 truncate text-[10px] text-red-300" title={error}>{error}</span>}
  </div>;
}
