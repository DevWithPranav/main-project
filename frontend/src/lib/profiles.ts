// Profile helpers: unwrap backend rows, validate against schemas/profile.schema.json before PUT,
// and warn about parameter names the engine does not know (ml/violation_engine/profiles.py rejects them).

import Ajv2020 from 'ajv/dist/2020';
import profileSchema from '../../../schemas/profile.schema.json';
import defaultProfile from '../../../ml/violation_engine/configs/profiles/default.json';
import type { Profile, ProfileVersion } from '../api/types';

const ajv = new Ajv2020({ allErrors: true, strict: false });
const validateFn = ajv.compile(profileSchema);

export const DEFAULT_PROFILE = defaultProfile as unknown as Profile;

export function isProfile(x: unknown): x is Profile {
  return !!x && typeof x === 'object' && 'profile_version' in x && 'name' in x;
}

/** A row from /api/profiles(/{name}) may be the profile itself or `{profile, version, ...}`. */
export function unwrapProfile(x: Profile | ProfileVersion | null | undefined): Profile | null {
  if (!x) return null;
  if (isProfile(x)) return x;
  const inner = (x as ProfileVersion).profile;
  return isProfile(inner) ? inner : null;
}

export function profileName(x: Profile | ProfileVersion): string {
  const p = unwrapProfile(x);
  return p?.name ?? String((x as ProfileVersion).name ?? '?');
}

export interface ValidationIssue {
  path: string;
  message: string;
  level: 'error' | 'warning';
}

export function validateProfile(p: unknown): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  if (!validateFn(p)) {
    for (const e of validateFn.errors ?? []) {
      const extra =
        e.keyword === 'additionalProperties'
          ? ` (${(e.params as { additionalProperty: string }).additionalProperty})`
          : e.keyword === 'enum'
            ? ` (${(e.params as { allowedValues: unknown[] }).allowedValues.join(', ')})`
            : '';
      issues.push({ path: e.instancePath || '/', message: `${e.message ?? 'invalid'}${extra}`, level: 'error' });
    }
  }
  // Unknown params: the schema allows any name, the engine does not.
  const violations = (p as Profile)?.violations ?? {};
  for (const [type, cfg] of Object.entries(violations)) {
    const known = DEFAULT_PROFILE.violations?.[type as keyof typeof DEFAULT_PROFILE.violations]?.params;
    if (!known || !cfg?.params) continue;
    for (const k of Object.keys(cfg.params)) {
      if (!(k in known))
        issues.push({ path: `/violations/${type}/params/${k}`, message: 'not a default parameter of this rule', level: 'warning' });
    }
  }
  return issues;
}

/** Plain-language label for a parameter name: `min_back_m` -> `min back (m)`. */
export function paramLabel(k: string): string {
  const units: [RegExp, string][] = [
    [/_kmh$/, 'km/h'],
    [/_mps$/, 'm/s'],
    [/_deg$/, 'deg'],
    [/_s$/, 's'],
    [/_m$/, 'm'],
  ];
  for (const [re, u] of units) if (re.test(k)) return `${k.replace(re, '').replace(/_/g, ' ')} (${u})`;
  return k.replace(/_/g, ' ');
}

/** Deep clone that also strips undefined. */
export function cloneProfile(p: Profile): Profile {
  return JSON.parse(JSON.stringify(p)) as Profile;
}

/** Paths whose value differs between two JSON values (for the "unsaved changes" summary). */
export function diffPaths(a: unknown, b: unknown, path = ''): string[] {
  if (a === b) return [];
  if (typeof a !== 'object' || typeof b !== 'object' || a === null || b === null || Array.isArray(a) !== Array.isArray(b))
    return [path || '/'];
  if (Array.isArray(a)) return JSON.stringify(a) === JSON.stringify(b) ? [] : [path || '/'];
  const keys = new Set([...Object.keys(a), ...Object.keys(b as object)]);
  const out: string[] = [];
  for (const k of keys)
    out.push(...diffPaths((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k], `${path}/${k}`));
  return out;
}
