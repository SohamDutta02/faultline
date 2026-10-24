import type { Architecture, HarnessSpec, Meta, Slot } from "./types";

export const SLOTS: Slot[] = ["instructions", "tools", "environment", "state", "feedback"];
export const SCRIPTED_SLOTS: Slot[] = ["tools", "environment"];

export function sameSpec(a: HarnessSpec, b: HarnessSpec, slots: Slot[] = SLOTS): boolean {
  return slots.every((s) => a[s] === b[s]);
}

export function presetsFor(meta: Meta, scripted: boolean): Architecture[] {
  return meta.architectures.filter((a) => a.needs_llm === !scripted);
}

/** The id the backend uses for a harness: a preset's name if one matches, else
 * the base (single, or oracle for a script) plus the slots that differ. */
export function harnessName(meta: Meta, spec: HarnessSpec, scripted: boolean): string {
  const slots = scripted ? SCRIPTED_SLOTS : SLOTS;
  const presets = presetsFor(meta, scripted);
  const match = presets.find((p) => sameSpec(p.spec, spec, slots));
  if (match) return match.id;
  const baseId = scripted ? "oracle" : "single";
  const base = presets.find((p) => p.id === baseId)?.spec ?? baselineSpec(meta);
  return baseId + slots.filter((s) => spec[s] !== base[s]).map((s) => `+${s}=${spec[s]}`).join("");
}

export function baselineSpec(meta: Meta): HarnessSpec {
  return Object.fromEntries(meta.harness.slots.map((s) => [s.id, s.baseline])) as HarnessSpec;
}

export function optionDescription(meta: Meta, slot: Slot, option: string): string {
  return meta.harness.slots.find((s) => s.id === slot)?.options.find((o) => o.id === option)?.description ?? "";
}

/** A signed number of percentage points, with a real minus sign. */
export function signed(n: number, digits = 0): string {
  const v = Number(n.toFixed(digits));
  if (v === 0) return (0).toFixed(digits);
  return `${v > 0 ? "+" : "−"}${Math.abs(v).toFixed(digits)}`;
}

export function tone(delta: number): string {
  return delta >= 5 ? "good" : delta <= -5 ? "critical" : "";
}

export const CAUSE_LABEL: Record<string, string> = {
  crash: "crash",
  feedback_broke_it: "feedback broke it",
  loop: "loop",
  duplicate_write: "duplicate write",
  lost_write: "lost write",
  trusted_bad_data: "trusted bad data",
  gave_up_on_errors: "gave up on errors",
  misread_write: "misread write",
  reasoning_error: "reasoning error",
  unattributed: "unattributed",
};
