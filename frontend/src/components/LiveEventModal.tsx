import { useEffect } from "react";

export default function LiveEventModal({
  title, subtitle, onClose, children, alerts, alertsPresent = false,
}: {
  title: string;
  subtitle: string;
  onClose: () => void;
  children: React.ReactNode;
  alerts?: React.ReactNode;
  alertsPresent?: boolean;
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
        className="fixed inset-0 z-40 bg-black/80 backdrop-blur-[1px]"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`fixed top-8 z-40 flex max-h-[88vh] flex-col overflow-hidden rounded-md border border-zinc-700 bg-zinc-950 shadow-2xl ${
          alertsPresent
            ? "left-3 right-[19.5rem]"
            : "inset-x-0 mx-auto w-[min(56rem,94vw)]"
        }`}
      >
        <div className="flex h-11 shrink-0 items-center gap-2 border-b border-zinc-800 bg-zinc-900 px-3">
          <span className="truncate text-sm font-bold text-zinc-100">{title}</span>
          <span className="microlabel truncate">{subtitle}</span>
          <span className="flex-1" />
          <button onClick={onClose} className="btn btn-ghost h-7 px-2" title="Kapat (Esc)">
            kapat ✕
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-2">{children}</div>
      </div>
      {alerts}
    </>
  );
}
