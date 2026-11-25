// The detection rule form: values are shown in people's units (percent, seconds) and checked against the same
// ranges as the server (app/routes/rules.py) before saving.
import type { Rule, Severity, ZoneType } from "./types";

export interface RuleFormValues {
  enabled: boolean;
  confidence: string; // percent, as typed
  window: string; // seconds
  share: string; // percent
  endGap: string; // seconds
  cooldown: string; // seconds
  severity: Severity;
  zoneIds: number[];
}

export type RuleFormErrors = Partial<Record<keyof RuleFormValues, string>>;

/** Ranges from the server, in the units shown on the form. */
export const RANGES = {
  confidence: { min: 5, max: 95, unit: "%" },
  window: { min: 1, max: 30, unit: "s" },
  share: { min: 10, max: 100, unit: "%" },
  endGap: { min: 1, max: 120, unit: "s" },
  cooldown: { min: 0, max: 600, unit: "s" },
} as const;

/** Which kind of area a hazard is checked in (config/defaults.yaml rules.zone_type). */
export function zoneTypeFor(hazardId: string): ZoneType {
  return hazardId === "H-05" ? "waste" : "prep";
}

const round1 = (v: number) => String(Math.round(v * 10) / 10);

export function formFromRule(r: Rule): RuleFormValues {
  return {
    enabled: r.enabled, confidence: round1(r.conf_threshold * 100), window: round1(r.window_s),
    share: round1(r.min_ratio * 100), endGap: round1(r.end_gap_s), cooldown: round1(r.cooldown_s),
    severity: r.severity, zoneIds: [...r.zone_ids],
  };
}

export function validateRuleForm(v: RuleFormValues): RuleFormErrors {
  const errors: RuleFormErrors = {};
  for (const key of Object.keys(RANGES) as (keyof typeof RANGES)[]) {
    const { min, max, unit } = RANGES[key];
    const raw = v[key].trim();
    const n = Number(raw);
    if (raw === "" || !Number.isFinite(n)) errors[key] = "Enter a number.";
    else if (n < min || n > max) errors[key] = `Use a value from ${min} to ${max} ${unit}.`;
  }
  if (v.zoneIds.length === 0) errors.zoneIds = "Choose at least one area.";
  return errors;
}

/** PATCH body with only what changed (server units: fractions and seconds). */
export function rulePatch(v: RuleFormValues, r: Rule): Record<string, unknown> {
  const next: Record<string, unknown> = {
    enabled: v.enabled,
    conf_threshold: Number(v.confidence) / 100,
    window_s: Number(v.window),
    min_ratio: Number(v.share) / 100,
    end_gap_s: Number(v.endGap),
    cooldown_s: Number(v.cooldown),
    severity: v.severity,
  };
  const body: Record<string, unknown> = {};
  for (const [k, value] of Object.entries(next)) {
    const old = r[k as keyof Rule];
    if (typeof value === "number" ? Math.abs(value - (old as number)) > 1e-9 : value !== old) body[k] = value;
  }
  const sorted = [...v.zoneIds].sort((a, b) => a - b);
  if (sorted.join(",") !== [...r.zone_ids].sort((a, b) => a - b).join(",")) body.zone_ids = sorted;
  return body;
}
