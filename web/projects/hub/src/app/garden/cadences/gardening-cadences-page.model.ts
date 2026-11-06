import type { CadenceCreateRequest, CadenceView } from 'fleet';

import type { CadenceDraft } from './cadence-form';
import type { CadenceCountVm, CadenceRowVm } from './cadence-list';
import { ERRAND_STATES, errandStateLook } from './cadence-vocabulary';

/** The line the list shows while the `cadences` flag is off: the records are served and editable, but mint nothing. */
export const CADENCES_FLAG_OFF_LINE =
  'The cadences flag is off: cadences are kept and editable here, but mint no run until it is turned on.';

/** The flag-off line while minting is off, `null` while it is on. */
export function flagOffLine(minting: boolean): string | null {
  return minting ? null : CADENCES_FLAG_OFF_LINE;
}

/** The effective selection: `slug` while it still names a loaded cadence, else `null`. */
export function presentCadenceSlug(slug: string | null, cadences: readonly CadenceView[]): string | null {
  if (slug === null) return null;
  return cadences.some((c) => c.slug === slug) ? slug : null;
}

/** A spacing's minutes in the largest whole unit: `10 days`, `6 hours`, `90 minutes`. */
export function spacingText(minutes: number): string {
  const [n, unit] =
    minutes % 1440 === 0 ? [minutes / 1440, 'day'] : minutes % 60 === 0 ? [minutes / 60, 'hour'] : [minutes, 'minute'];
  return `${n} ${unit}${n === 1 ? '' : 's'}`;
}

/**
 * A cadence's rhythm as one line: its schedule's cron and zone, then its spacing — `0 20 * * * America/Chicago, at
 * least 10 days apart`. A spacing counted from other than its start names its anchor.
 */
export function rhythmText(cadence: Pick<CadenceView, 'schedule' | 'spacing_minutes' | 'anchor'>): string {
  const parts: string[] = [];
  if (cadence.schedule !== null) parts.push(`${cadence.schedule.cron} ${cadence.schedule.zone}`);
  if (cadence.spacing_minutes !== null) {
    const anchored = cadence.anchor === null || cadence.anchor === 'started' ? '' : ` from ${cadence.anchor}`;
    parts.push(`at least ${spacingText(cadence.spacing_minutes)} apart${anchored}`);
  }
  return parts.length === 0 ? 'no rhythm' : parts.join(', ');
}

/** A cadence's errands counted by state, the states holding none left out. */
export function errandCounts(cadence: CadenceView): readonly CadenceCountVm[] {
  const counts = cadence.errand_counts;
  return ERRAND_STATES.filter((state) => counts[state] > 0).map((state) => ({
    state,
    count: counts[state],
    ...errandStateLook(state),
  }));
}

/** The cadence list's rows: each cadence's name, rhythm, next moment, chores, errand counts, and attention. */
export function cadenceRows(cadences: readonly CadenceView[]): readonly CadenceRowVm[] {
  return cadences.map((c) => ({
    slug: c.slug,
    name: c.name,
    retired: c.retired,
    rhythm: rhythmText(c),
    nextMoment: c.next_moments[0] ?? null,
    choreCount: c.chores.length,
    counts: errandCounts(c),
    attentionCount: c.attention.length,
  }));
}

/** An empty cadence draft: no slug, no name, no rhythm. */
export const EMPTY_CADENCE_DRAFT: CadenceDraft = { slug: '', name: '', cron: '', zone: '', spacing: '', anchor: '' };

/** The create body a draft states: a field left blank is left out, so the hub's own default applies. */
export function createCadenceRequest(draft: CadenceDraft): CadenceCreateRequest {
  const cron = draft.cron.trim();
  const zone = draft.zone.trim();
  return {
    slug: draft.slug.trim(),
    ...(draft.name.trim() ? { name: draft.name.trim() } : {}),
    ...(cron || zone ? { schedule: { cron: cron || null, zone: zone || null } } : {}),
    ...(draft.spacing.trim() ? { spacing: draft.spacing.trim() } : {}),
    ...(draft.anchor ? { anchor: draft.anchor } : {}),
  };
}
