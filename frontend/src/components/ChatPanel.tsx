import { useEffect, useRef, useState } from "react";
import type { ChatMessage } from "../types/events";

/** Operatör sohbeti — basit panel. */
export default function ChatPanel({
  messages, onSend,
}: { messages: ChatMessage[]; onSend: (text: string) => void }) {
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const submit = () => {
    const text = draft.trim();
    if (!text) return;
    onSend(text);
    setDraft("");
  };

  return (
    <div className="panel h-full">
      <div className="panel-title">Operatör Sohbeti</div>
      <div className="flex-1 overflow-y-auto p-2 space-y-2">
        {messages.map((m, i) => (
          <div
            key={i}
            className={`max-w-[85%] rounded-lg px-3 py-1.5 text-sm ${
              m.role === "operator"
                ? "ml-auto bg-sky-900/50 text-sky-100"
                : "bg-zinc-800 text-zinc-200"
            }`}
          >
            {m.text}
            {m.streaming && <span className="animate-pulse">▍</span>}
          </div>
        ))}
        <div ref={endRef} />
      </div>
      <div className="p-2 border-t border-zinc-800 flex gap-2 shrink-0">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          placeholder="Ajana sor: '00:15'te ne oldu?'"
          className="flex-1 rounded bg-zinc-800 border border-zinc-700 px-3 py-1.5 text-sm outline-none focus:border-zinc-500"
        />
        <button
          onClick={submit}
          className="rounded bg-sky-700 hover:bg-sky-600 px-3 py-1.5 text-sm font-medium"
        >
          Gönder
        </button>
      </div>
    </div>
  );
}
