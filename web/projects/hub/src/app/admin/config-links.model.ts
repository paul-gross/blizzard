import { RecordKind, type RecordRefView } from 'fleet';

import type { ConfigLinkVm } from './config-record-panel';

/** The admin route segment each record kind's surface lives at. */
const SURFACE_SEGMENT: Record<RecordKind, string> = {
  [RecordKind.WORK_SOURCE]: 'work-sources',
  [RecordKind.REPOSITORY]: 'repositories',
  [RecordKind.SECRET]: 'secrets',
};

/** A record kind as text — the wire value with its underscores spaced. */
export function recordKindLabel(kind: string): string {
  return kind.replaceAll('_', ' ');
}

/** The router commands that open `key` of `kind`, or `null` for a kind the board has
 * no surface for. */
export function recordRoute(kind: string, key: string): readonly string[] | null {
  const segment = (SURFACE_SEGMENT as Record<string, string | undefined>)[kind];
  return segment === undefined ? null : ['/admin', segment, key];
}

/** A link to the record `ref` names. */
export function recordLink(ref: RecordRefView): ConfigLinkVm {
  return { kind: recordKindLabel(ref.kind), name: ref.key, route: recordRoute(ref.kind, ref.key) };
}

/** A link to the secret `name`, or none when the record names no secret. */
export function secretLinks(name: string | null | undefined): readonly ConfigLinkVm[] {
  return name ? [recordLink({ kind: RecordKind.SECRET, key: name })] : [];
}

/** A nullable field as text — `—` when unset. */
export function factText(value: string | null | undefined): string {
  return value === null || value === undefined || value === '' ? '—' : value;
}
