import { memo, useState, type ReactNode } from "react";
import type { ChatMessage } from "../types/events";
import { useStickyScroll } from "../lib/useStickyScroll";

const INLINE = /(\*\*[^*]+\*\*|`[^`]+`)/g;

function inlineNodes(text: string, keyBase: string): ReactNode[] {
  return text.split(INLINE).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return <strong key={`${keyBase}:${i}`} className="font-semibold text-zinc-50">{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      return <code key={`${keyBase}:${i}`} className="rounded-sm bg-zinc-950/80 px-1 font-mono text-[11px]">{part.slice(1, -1)}</code>;
    }
    return part;
  });
}

const ChatText = memo(function ChatText({ text }: { text: string }) {
  return (
    <>
      {text.split("\n").map((line, i) => {
        const item = line.match(/^\s*([*-]|\d+[.)])\s+(.*)$/);
        if (item) {
          return (
            <span key={i} className="flex gap-1.5 pl-1">
              <span className="shrink-0 text-zinc-500">{item[1] === "*" || item[1] === "-" ? "•" : item[1]}</span>
              <span className="min-w-0">{inlineNodes(item[2], `l${i}`)}</span>
            </span>
          );
        }
        if (!line.trim()) return <span key={i} className="block h-1.5" />;
        return <span key={i} className="block">{inlineNodes(line, `l${i}`)}</span>;
      })}
    </>
  );
});

function ChatPanel({
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
                ? "ml-auto whitespace-pre-wrap bg-sky-950 text-sky-100"
                : "bg-zinc-800 text-zinc-200"
            }`}
          >
            {m.role === "agent" ? <ChatText text={m.text} /> : m.text}
            {m.streaming && <span className="animate-pulse text-sky-400">▍</span>}
          </div>
        ))}
      </div>
      <div className="flex shrink-0 gap-1.5 border-t border-zinc-800 p-2">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          placeholder="Ajana sor: 00:15’te ne oldu?"
          className="field h-7 min-w-0 flex-1"
        />
        <button onClick={submit} className="btn btn-accent">
          Gönder
        </button>
      </div>
    </div>
  );
}

export default memo(ChatPanel);
