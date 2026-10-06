import { RecordKind, type RecordRefView } from 'fleet';

import type { ConfigLinkVm } from './config-record-panel';

/** The route each record kind's surface lives at — the fleet's records under admin, the
 * gardening records under gardening. */
const SURFACE_SEGMENT: Record<RecordKind, readonly string[]> = {
  [RecordKind.WORK_SOURCE]: ['/admin', 'work-sources'],
  [RecordKind.REPOSITORY]: ['/admin', 'repositories'],
  [RecordKind.SECRET]: ['/admin', 'secrets'],
  [RecordKind.SCOPE]: ['/gardening', 'scopes'],
  [RecordKind.ROUTINE]: ['/gardening', 'routines'],
};

/** A record kind as text — the wire value with its underscores spaced. */
export function recordKindLabel(kind: string): string {
  return kind.replaceAll('_', ' ');
}

/** The router commands that open `key` of `kind`, or `null` for a kind the board has
 * no surface for. */
export function recordRoute(kind: string, key: string): readonly string[] | null {
  const surface = (SURFACE_SEGMENT as Record<string, readonly string[] | undefined>)[kind];
  return surface === undefined ? null : [...surface, key];
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
