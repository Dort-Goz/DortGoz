import { useEffect } from "react";

export default function LiveEventModal({
  title, subtitle, onClose, children, alerts, badge,
}: {
  title: string;
  subtitle: string;
  onClose: () => void;
  children: React.ReactNode;
  alerts?: React.ReactNode;
  badge?: React.ReactNode;
}) {
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [onClose]);

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
        aria-label={title}
        className="fixed inset-x-0 top-1/2 z-50 mx-auto flex max-h-[92vh] w-[min(72rem,94vw)] -translate-y-1/2 flex-col overflow-hidden rounded-md border border-zinc-700 bg-zinc-950 shadow-2xl"
      >
        <div className="flex h-10 shrink-0 items-center gap-2 border-b border-zinc-800 bg-zinc-900 px-3">
          {badge}
          <span className="truncate text-sm font-bold text-zinc-100">{title}</span>
          <span className="microlabel truncate">{subtitle}</span>
          <span className="flex-1" />
          <button onClick={onClose} className="btn btn-ghost" title="Kapat (Esc)">
            ✕ kapat
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-2">{children}</div>
      </div>
      {alerts}
    </>
  );
}
