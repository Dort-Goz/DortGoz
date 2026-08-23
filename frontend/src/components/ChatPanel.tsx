import { useState } from "react";
import type { ChatMessage } from "../types/events";
import { useStickyScroll } from "../lib/useStickyScroll";

export default function ChatPanel({
  messages, onSend,
}: { messages: ChatMessage[]; onSend: (text: string) => void }) {
  const [draft, setDraft] = useState("");
  const { ref, onScroll } = useStickyScroll<HTMLDivElement>(messages);

  const submit = () => {
    const text = draft.trim();
    if (!text) return;
    onSend(text);
    setDraft("");
  };

  return (
    <div className="panel h-full">
      <div className="panel-title">
        <span>Operatör Sohbeti</span>
      </div>
      <div ref={ref} onScroll={onScroll} className="panel-body space-y-1.5 p-2">
        {messages.length === 0 && (
          <p className="p-2 text-xs text-zinc-600">
            Ajana soru sorun — koşu sırasında ve sonrasında yanıtlar.
          </p>
        )}
        {messages.map((m, i) => (
          <div
            key={i}
            className={`max-w-[85%] rounded-md px-2.5 py-1.5 text-xs leading-relaxed ${
              m.role === "operator"
                ? "ml-auto bg-sky-950 text-sky-100"
                : "bg-zinc-800 text-zinc-200"
            }`}
          >
            {m.text}
            {m.streaming && <span className="animate-pulse text-sky-400">▍</span>}
          </div>
        ))}
      </div>
      <div className="flex shrink-0 gap-1.5 border-t border-zinc-800 p-2">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          placeholder="Ajana sor: '00:15'te ne oldu?'"
          className="field h-7 min-w-0 flex-1"
        />
        <button onClick={submit} className="btn btn-accent">
          Gönder
        </button>
      </div>
    </div>
  );
}
