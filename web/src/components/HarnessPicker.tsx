import { useEffect, useState } from "react";
import { api } from "../api";
import { SCRIPTED_SLOTS, SLOTS, baselineSpec, harnessName, optionDescription, presetsFor } from "../harness";
import { hasModel, useFetch, useMeta } from "../hooks";
import type { Architecture, HarnessSpec, Slot } from "../types";

/** Pick a preset, or build a harness slot by slot. Reports the harness id. */
export function HarnessPicker({
  value,
  onChange,
  disabled,
}: {
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
}) {
  const meta = useMeta();
  const isPreset = meta.architectures.some((a) => a.id === value);
  const [mode, setMode] = useState<"presets" | "build">(isPreset ? "presets" : "build");
  const [scripted, setScripted] = useState(false);
  const [spec, setSpec] = useState<HarnessSpec>(
    meta.architectures.find((a) => a.id === value)?.spec ?? baselineSpec(meta),
  );

  // an id from a link (?arch=...) that isn't a preset: load its spec into the builder
  useEffect(() => {
    const preset = meta.architectures.find((a) => a.id === value);
    if (preset) {
      setSpec(preset.spec);
      setScripted(!preset.needs_llm);
      return;
    }
    api
      .get<Architecture>(`/api/architectures/${encodeURIComponent(value)}`)
      .then((a) => {
        setSpec(a.spec);
        setScripted(!a.needs_llm);
        setMode("build");
      })
      .catch(() => undefined);
  }, [value, meta.architectures]);

  const info = useFetch<Architecture>(mode === "build" ? `/api/architectures/${encodeURIComponent(value)}` : null);
  const editable: Slot[] = scripted ? SCRIPTED_SLOTS : SLOTS;

  function build(next: HarnessSpec, nextScripted = scripted) {
    setSpec(next);
    setScripted(nextScripted);
    onChange(harnessName(meta, next, nextScripted));
  }

  return (
    <div className="stack" style={{ gap: 10 }}>
      <div className="segmented" role="tablist" aria-label="harness mode">
        <button role="tab" aria-selected={mode === "presets"} className={mode === "presets" ? "on" : ""}
          onClick={() => setMode("presets")} disabled={disabled}>
          Presets
        </button>
        <button role="tab" aria-selected={mode === "build"} className={mode === "build" ? "on" : ""}
          onClick={() => setMode("build")} disabled={disabled}>
          Build
        </button>
      </div>

      {mode === "presets" ? (
        <div className="options" role="radiogroup" aria-label="harness preset">
          {meta.architectures.map((a) => {
            const ok = !a.needs_llm || hasModel(meta);
            return (
              <label key={a.id} className={`option${a.id === value ? " selected" : ""}${ok ? "" : " disabled"}`}>
                <input type="radio" name="arch" checked={a.id === value} disabled={!ok || disabled}
                  onChange={() => onChange(a.id)} />
                <span className="option-title">
                  <span className="mono">{a.id}</span>
                  <span className="tag">{a.needs_llm ? "model" : "scripted"}</span>
                </span>
                <span className="option-desc">{ok ? a.description : "needs a model key"}</span>
              </label>
            );
          })}
        </div>
      ) : (
        <div className="stack" style={{ gap: 10 }}>
          <label className="field">
            <span className="label">Driven by</span>
            <select value={scripted ? "script" : "model"} disabled={disabled}
              onChange={(e) => {
                const s = e.target.value === "script";
                const base = presetsFor(meta, s).find((p) => p.id === (s ? "oracle" : "single"))?.spec ?? spec;
                build(s ? { ...base, tools: spec.tools, environment: spec.environment } : spec, s);
              }}>
              <option value="model" disabled={!hasModel(meta)}>
                model{hasModel(meta) ? "" : " (needs a model key)"}
              </option>
              <option value="script">script: the oracle control</option>
            </select>
          </label>
          {meta.harness.slots.map((slot) => {
            const can = editable.includes(slot.id);
            return (
              <label key={slot.id} className="field">
                <span className="label" title={slot.description}>{slot.id}</span>
                <select value={spec[slot.id]} disabled={disabled || !can}
                  onChange={(e) => build({ ...spec, [slot.id]: e.target.value })}>
                  {slot.options.map((o) => (
                    <option key={o.id} value={o.id}>
                      {o.id}
                      {o.id === slot.baseline ? " (baseline)" : ""}
                    </option>
                  ))}
                </select>
                <span className="secondary" style={{ fontSize: 12 }}>
                  {can ? optionDescription(meta, slot.id, spec[slot.id]) : "a script has no model to instruct"}
                </span>
              </label>
            );
          })}
          <div className="harness-id">
            <span className="label">Harness</span>
            <code className="mono">{value}</code>
            {info.data && (
              <span className="muted mono" title="fingerprint: prompts, tool schemas, policies and option versions">
                {info.data.fingerprint}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
