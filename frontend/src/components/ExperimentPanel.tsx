/** Deney paneli — model seçimi + sistem/görev istemi düzenleme.
 *
 *  Yorumlama hattının üç serbestlik derecesini koşu başına ayarlar; boş/varsayılan
 *  bırakılan alanlar backend şablonlarına düşer. Görev istemindeki {start}/{end}
 *  yer tutucuları pencere sınırlarıyla doldurulur — silinirse pencere bağlamı
 *  kaybolur, panel bunu uyarıyla gösterir.
 */

export interface InterpretConfig {
  default_model: string;
  models: string[];
  system_prompt: string;
  task_prompt: string;
}

interface Props {
  config: InterpretConfig;
  model: string;
  systemPrompt: string;
  taskPrompt: string;
  busy: boolean;
  onModel: (v: string) => void;
  onSystemPrompt: (v: string) => void;
  onTaskPrompt: (v: string) => void;
}

function PromptBox({ label, value, fallback, disabled, onChange, warn }: {
  label: string;
  value: string;
  fallback: string;
  disabled: boolean;
  onChange: (v: string) => void;
  warn?: string;
}) {
  const customized = value !== fallback;
  return (
    <div className="flex-1 min-w-0 flex flex-col gap-1">
      <div className="flex items-center gap-2 text-[11px] uppercase tracking-wide text-zinc-500">
        {label}
        {customized && <span className="text-amber-400 normal-case">— özelleştirildi</span>}
        {customized && (
          <button
            onClick={() => onChange(fallback)}
            disabled={disabled}
            className="ml-auto text-zinc-400 hover:text-zinc-200 disabled:opacity-40 normal-case"
          >
            varsayılana dön
          </button>
        )}
      </div>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        spellCheck={false}
        rows={6}
        className="w-full resize-y rounded border border-zinc-700 bg-zinc-950/60 p-2
                   font-mono text-xs leading-relaxed text-zinc-200
                   focus:border-emerald-700 focus:outline-none disabled:opacity-50"
      />
      {warn && <div className="text-[11px] text-amber-400">{warn}</div>}
    </div>
  );
}

export default function ExperimentPanel({
  config, model, systemPrompt, taskPrompt, busy,
  onModel, onSystemPrompt, onTaskPrompt,
}: Props) {
  const placeholderLost =
    !taskPrompt.includes("{start}") || !taskPrompt.includes("{end}");
  return (
    <div className="shrink-0 rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2
                    flex flex-col gap-2 text-sm">
      <div className="flex items-center gap-2 text-xs text-zinc-400">
        <span className="text-[11px] uppercase tracking-wide font-bold text-zinc-500">
          Deney
        </span>
        <label className="ml-2">model</label>
        <select
          value={model}
          onChange={(e) => onModel(e.target.value)}
          disabled={busy}
          className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 max-w-72
                     disabled:opacity-50"
        >
          {config.models.map((m) => (
            <option key={m} value={m}>
              {m}{m === config.default_model ? " (varsayılan)" : ""}
            </option>
          ))}
        </select>
        <span className="text-zinc-600">
          seçenekler bir sonraki koşuda geçerli olur; etkin yapılandırma
          runs/&lt;id&gt;.meta.json'a yazılır
        </span>
      </div>
      <div className="flex gap-3 flex-col lg:flex-row">
        <PromptBox
          label="Sistem istemi"
          value={systemPrompt}
          fallback={config.system_prompt}
          disabled={busy}
          onChange={onSystemPrompt}
        />
        <PromptBox
          label="Görev istemi (kullanıcı)"
          value={taskPrompt}
          fallback={config.task_prompt}
          disabled={busy}
          onChange={onTaskPrompt}
          warn={placeholderLost
            ? "⚠ {start}/{end} yer tutucuları eksik — model pencere sınırlarını göremez"
            : undefined}
        />
      </div>
    </div>
  );
}
