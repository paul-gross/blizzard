// exemplar: bzh:frontend-domain-boundary, bzh:frontend-edit-baseline, bzh:frontend-mutation-settles-on-refresh.

import {
  type CadenceEditRequest,
  type CadenceView,
  CadenceAnchor as WireCadenceAnchor,
  type EditBaseline,
  openLookup,
  type RefusalMap,
} from 'fleet';

import type { Cadence, CadenceAnchor, CadenceDraft, UnlistedAnchor } from './cadence.domain';

const anchorOf = openLookup<WireCadenceAnchor, CadenceAnchor | UnlistedAnchor>(
  {
    [WireCadenceAnchor.SCHEDULED]: 'scheduled',
    [WireCadenceAnchor.STARTED]: 'started',
    [WireCadenceAnchor.FINISHED]: 'finished',
  },
  (raw) => ({ unlisted: raw }),
);

/** A served cadence as the domain holds it. */
export function fromCadenceView(wire: CadenceView): Cadence {
  return {
    id: wire.cadence_id,
    revision: wire.revision,
    slug: wire.slug,
    name: wire.name,
    schedule: wire.schedule === null ? null : { cron: wire.schedule.cron, zone: wire.schedule.zone },
    spacingMinutes: wire.spacing_minutes,
    anchor: wire.anchor === null ? null : anchorOf(wire.anchor),
  };
}

function spacingText(minutes: number | null): string {
  if (minutes === null) return '';
  if (minutes % 1440 === 0) return `${minutes / 1440}d`;
  if (minutes % 60 === 0) return `${minutes / 60}h`;
  return `${minutes}m`;
}

/** What an edit of `cadence` starts from: its id, the revision it was read at, and its draft, all from that one read.
 * An anchor this build does not know opens unstated; an edit leaving it untouched never sends it. */
export function toEditBaseline(cadence: Cadence): EditBaseline<CadenceDraft> {
  return {
    id: cadence.id,
    revision: cadence.revision,
    draft: {
      name: cadence.name,
      cron: cadence.schedule?.cron ?? '',
      zone: cadence.schedule?.zone ?? '',
      spacing: spacingText(cadence.spacingMinutes),
      anchor: cadence.anchor === null || typeof cadence.anchor !== 'string' ? 'unstated' : cadence.anchor,
    },
  };
}

const REFUSAL_FIELDS: Readonly<Record<string, keyof CadenceDraft>> = {
  name: 'name',
  'schedule.cron': 'cron',
  'schedule.zone': 'zone',
  schedule: 'cron',
  spacing: 'spacing',
  anchor: 'anchor',
};

/** How the hub's cadence refusals reach the draft: a field path to its field; anything else to the form line. */
export const CADENCE_REFUSALS: RefusalMap<CadenceDraft> = {
  field: (path) => (Object.hasOwn(REFUSAL_FIELDS, path) ? REFUSAL_FIELDS[path] : null),
  kinds: {},
};

/** The edit body for `after` against the draft it started from: only the fields that changed. The schedule goes as a
 * pair, `null` once both halves are blank; a blank spacing is `null`; `unstated` is anchor `null`. */
export function toEditRequest(before: CadenceDraft, after: CadenceDraft): CadenceEditRequest {
  const name = after.name.trim();
  const cron = after.cron.trim();
  const zone = after.zone.trim();
  const spacing = after.spacing.trim();
  const scheduleChanged = before.cron.trim() !== cron || before.zone.trim() !== zone;
  return {
    ...(name !== before.name.trim() ? { name } : {}),
    ...(scheduleChanged ? { schedule: cron || zone ? { cron: cron || null, zone: zone || null } : null } : {}),
    ...(spacing !== before.spacing.trim() ? { spacing: spacing || null } : {}),
    ...(after.anchor !== before.anchor ? { anchor: after.anchor === 'unstated' ? null : after.anchor } : {}),
  };
}
