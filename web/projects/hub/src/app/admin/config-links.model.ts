import { openLookup } from 'fleet';
import type { ConfigRecordKind, ConfigRecordRef } from './config-history.domain';

import type { ConfigLinkVm } from './config-record-panel';

/** The route each record kind's surface lives at — the fleet's records under admin, the
 * gardening records under gardening. A kind the board has no surface for — a link kind, or one
 * the client does not know — reads `null`. */
const SURFACE_SEGMENT = openLookup<ConfigRecordKind, readonly string[] | null>(
  {
    work_source: ['/admin', 'work-sources'],
    repository: ['/admin', 'repositories'],
    forge: ['/admin', 'forges'],
    credential: ['/admin', 'credentials'],
    secret: ['/admin', 'secrets'],
    project: ['/admin', 'projects'],
    scope: ['/gardening', 'scopes'],
    routine: ['/gardening', 'routines'],
    cadence: ['/gardening', 'cadences'],
    project_source_link: null,
    project_repository_link: null,
    urgency: null,
    urgency_order: null,
    tenant_settings: null,
  },
  () => null,
);

/** A record kind as text — the wire value with its underscores spaced. */
export function recordKindLabel(kind: string): string {
  return kind.replaceAll('_', ' ');
}

/** The router commands that open `key` of `kind`, or `null` for a kind the board has
 * no surface for. */
export function recordRoute(kind: string, key: string): readonly string[] | null {
  const surface = SURFACE_SEGMENT(kind);
  return surface === null ? null : [...surface, key];
}

/** A link to the record `ref` names, shown by its label — a repository's or work source's current slug — when it has one. */
export function recordLink(ref: ConfigRecordRef): ConfigLinkVm {
  return { kind: recordKindLabel(ref.kind), name: ref.label ?? ref.key, route: recordRoute(ref.kind, ref.key) };
}

/** A link to the credential `id`, shown by its slug, or none when the record names no credential. */
export function credentialLinks(id: string | null | undefined, slug: string | null | undefined): readonly ConfigLinkVm[] {
  return id ? [recordLink({ kind: 'credential', key: id, label: slug ?? null })] : [];
}

/** A nullable field as text — `—` when unset. */
export function factText(value: string | null | undefined): string {
  return value === null || value === undefined || value === '' ? '—' : value;
}
