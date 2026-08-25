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
    <div className="flex min-w-0 flex-1 flex-col gap-1">
      <div className="flex items-center gap-2">
        <span className="microlabel">{label}</span>
        {customized && <span className="text-[10px] text-amber-400">— özelleştirildi</span>}
        {customized && (
          <button
            onClick={() => onChange(fallback)}
            disabled={disabled}
            className="ml-auto text-[10px] text-zinc-400 hover:text-zinc-200 disabled:opacity-40"
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
        className="field-area h-[120px] resize-y font-mono leading-5"
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
    <div className="flex shrink-0 flex-col gap-2 border-b border-amber-900/40 bg-zinc-900 px-3 py-2">
      <div className="flex items-center gap-2 text-xs text-zinc-400">
        <span className="microlabel text-amber-500">⚗ deney</span>
        <span className="microlabel ml-2">model</span>
        <select
          value={model}
          onChange={(e) => onModel(e.target.value)}
          disabled={busy}
          className="field max-w-72"
        >
          {config.models.map((m) => (
            <option key={m} value={m}>
              {m}{m === config.default_model ? " (varsayılan)" : ""}
            </option>
          ))}
        </select>
        <span className="truncate text-[11px] text-zinc-600">
          seçenekler bir sonraki koşuda geçerli olur; etkin yapılandırma
          runs/&lt;id&gt;.meta.json'a yazılır
        </span>
      </div>
      <div className="flex flex-col gap-3 lg:flex-row">
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
