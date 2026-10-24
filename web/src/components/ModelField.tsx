import { useMeta } from "../hooks";
import type { Provider } from "../types";

const OPENAI_PREFIXES = ["gpt-", "gpt4", "o1", "o3", "o4", "chatgpt"];

/** Mirrors agents/backends.provider_for: the model id's prefix picks the provider. */
export function providerFor(model: string): Provider["id"] {
  const m = model.toLowerCase();
  if (m.startsWith("gemini")) return "gemini";
  if (m.startsWith("claude")) return "anthropic";
  return OPENAI_PREFIXES.some((p) => m.startsWith(p)) || m ? "openai" : "gemini";
}

const NAME: Record<Provider["id"], string> = {
  gemini: "Google Gemini",
  anthropic: "Anthropic",
  openai: "an OpenAI-compatible server",
};

/** A model id, blank for the server's default; suggests the defaults of the providers that have a key. */
export function ModelField({
  value,
  onChange,
  disabled,
  id = "model",
}: {
  value: string;
  onChange: (model: string) => void;
  disabled?: boolean;
  id?: string;
}) {
  const meta = useMeta();
  const typed = value.trim();
  const provider = typed ? meta.providers.find((p) => p.id === providerFor(typed)) : undefined;
  const suggestions = meta.providers.filter((p) => p.available && p.default_model);
  return (
    <label className="field">
      <span className="label">Model</span>
      <input
        type="text"
        className="mono"
        list={`${id}-options`}
        placeholder={meta.model ? `default: ${meta.model}` : "model id"}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      />
      <datalist id={`${id}-options`}>
        {suggestions.map((p) => (
          <option key={p.id} value={p.default_model!} />
        ))}
      </datalist>
      <span className="secondary" style={{ fontSize: 12 }}>
        {provider
          ? provider.available
            ? `runs on ${NAME[provider.id]}`
            : `needs ${provider.key}`
          : `gemini-*, claude-*, gpt-*, or any model your OpenAI-compatible server serves`}
      </span>
    </label>
  );
}
