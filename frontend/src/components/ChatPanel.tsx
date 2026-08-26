import { memo, useEffect, useState, type ReactNode } from "react";
import { investigationQuestionsFor } from "../lib/investigationQuestions";
import type { StoredIncident } from "../state";
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
  messages, onSend, contextLabel, incident,
}: {
  messages: ChatMessage[];
  onSend: (text: string) => void;
  contextLabel?: string;
  incident?: StoredIncident | null;
}) {
  const [draft, setDraft] = useState("");
  const [showQuestions, setShowQuestions] = useState(false);
  const { ref, onScroll } = useStickyScroll<HTMLDivElement>(messages);
  const questionSet = incident ? investigationQuestionsFor(incident) : null;

  useEffect(() => setShowQuestions(false), [incident?.incident_id]);

  const submit = () => {
    const text = draft.trim();
    if (!text) return;
    onSend(text);
    setDraft("");
  };

  return (
    <div className="panel h-full">
      <div className="panel-title flex items-center gap-2">
        <span>Operatör Sohbeti</span>
        {questionSet && (
          <button
            type="button"
            onClick={() => setShowQuestions((visible) => !visible)}
            aria-expanded={showQuestions}
            title="Seçili olayı üç genel ve iki kategori sorusuyla ayrıntılı incele"
            className={`ml-auto h-6 rounded-sm border px-2 normal-case tracking-normal transition-colors ${
              showQuestions
                ? "border-sky-700 bg-sky-950/60 text-sky-200"
                : "border-zinc-700 text-zinc-300 hover:border-sky-800 hover:text-sky-200"
            }`}
          >
            ✦ Olayı aydınlat
          </button>
        )}
        {contextLabel && (
          <span className={`${questionSet ? "" : "ml-auto"} normal-case font-normal text-[10px] text-zinc-500`}>
            bağlam: {contextLabel}
          </span>
        )}
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
      {showQuestions && questionSet && (
        <div className="shrink-0 border-t border-zinc-800 bg-zinc-950/70 p-2">
          <div className="mb-1.5 flex items-center gap-2">
            <span className="microlabel">3 genel · 2 olaya özel</span>
            <span className="min-w-0 truncate text-[10px] text-sky-400">
              {questionSet.profileLabel}
            </span>
            <button
              type="button"
              onClick={() => setShowQuestions(false)}
              aria-label="Olay sorularını kapat"
              className="ml-auto text-xs text-zinc-600 hover:text-zinc-300"
            >
              ×
            </button>
          </div>
          <div className="grid grid-cols-2 gap-1">
            {questionSet.questions.map((question, index) => (
              <button
                key={question.id}
                type="button"
                title={question.prompt}
                onClick={() => {
                  onSend(`Olayı aydınlat: ${question.prompt}`);
                  setShowQuestions(false);
                }}
                className={`min-h-8 rounded-sm border px-2 py-1 text-left text-[11px] leading-tight transition-colors ${
                  index === 2 ? "col-span-2 " : ""
                }${
                  question.scope === "category"
                    ? "border-sky-900/80 bg-sky-950/30 text-sky-200 hover:border-sky-700 hover:bg-sky-950/60"
                    : "border-zinc-800 bg-zinc-900 text-zinc-300 hover:border-zinc-600 hover:text-zinc-100"
                }`}
              >
                {question.label}
              </button>
            ))}
          </div>
        </div>
      )}
      <div className="flex shrink-0 gap-1.5 border-t border-zinc-800 p-2">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          placeholder={incident ? "Sorunuzu yazın veya 'Olayı aydınlat'ı açın" : "Ajana sorun"}
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
