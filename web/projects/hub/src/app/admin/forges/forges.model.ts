import { RecordKind, type ForgeCreateRequest, type ForgeDetail, type ForgeSummary } from 'fleet';
import { type ConfigFieldDef, createText } from '../config-edit.model';
import { filterByLifecycle, type LifecycleFilter } from '../config-filter.model';
import { factText, recordLink } from '../config-links.model';
import type { ConfigBadgeVm, ConfigRowVm } from '../config-record-list';
import type { ConfigRecordVm } from '../config-record-panel';

export const FORGE_FIELDS: readonly ConfigFieldDef<keyof ForgeCreateRequest>[] = [
  { key: 'slug', label: 'Slug', kind: 'text', required: true },
  { key: 'name', label: 'Name', kind: 'text' },
  { key: 'kind', label: 'Kind', kind: 'text', required: true, createOnly: true },
  { key: 'api_base', label: 'API base', kind: 'text', required: true },
  { key: 'web_base', label: 'Web base', kind: 'text' },
];

export function forgeCreate(body: Readonly<Record<string, string | boolean>>): ForgeCreateRequest {
  const text = (key: string) => createText(body, key);
  return {
    slug: text('slug'), kind: text('kind'), api_base: text('api_base'),
    ...(text('name') ? { name: text('name') } : {}),
    ...(text('web_base') ? { web_base: text('web_base') } : {}),
  };
}

export function forgeBadges(forge: ForgeSummary): readonly ConfigBadgeVm[] {
  return forge.retired ? [{ label: 'retired', tone: 'idle' }] : [];
}

export function forgeRows(forges: readonly ForgeSummary[], filter: LifecycleFilter): readonly ConfigRowVm[] {
  return filterByLifecycle(forges, filter).map((forge) => ({
    key: forge.forge_id, title: forge.slug,
    sub: [forge.name, forge.kind, forge.api_base],
    badges: forgeBadges(forge), revision: forge.revision, retired: forge.retired,
  }));
}

export function forgeRecordVm(forge: ForgeDetail | undefined): ConfigRecordVm | null {
  if (!forge) return null;
  return {
    name: forge.slug, badges: forgeBadges(forge), revision: forge.revision,
    facts: [
      { label: 'Name', value: forge.name }, { label: 'Kind', value: forge.kind },
      { label: 'API base', value: forge.api_base }, { label: 'Web base', value: factText(forge.web_base) },
      { label: 'Web origin', value: forge.web_origin },
      { label: 'Created by', value: forge.created_by_name ?? forge.created_by },
    ],
    note: forge.retired ? 'Retired — unavailable for new records.' : null,
    links: {
      heading: 'Used by',
      links: [
        ...forge.repositories.map((ref) => recordLink({ ...ref, kind: RecordKind.REPOSITORY })),
        ...forge.work_sources.map((ref) => recordLink({ ...ref, kind: RecordKind.WORK_SOURCE })),
      ],
      emptyText: 'No repositories or work sources use this forge.',
    },
    hasHistory: true,
  };
}

/** Only enabled forges are offered. A source's provider must match the forge's kind. */
export function forgeOptions(forges: readonly ForgeSummary[], provider?: string): readonly { value: string; label: string }[] {
  return forges.filter((forge) => !forge.retired && (provider === undefined || forge.kind === provider))
    .map((forge) => ({ value: forge.forge_id, label: `${forge.slug} · ${forge.name}` }));
}

export function forgePickerFields<K extends string>(fields: readonly ConfigFieldDef<K>[], forges: readonly ForgeSummary[], provider?: string): readonly ConfigFieldDef<K>[] {
  return fields.map((field) => field.key === 'forge' ? {
    ...field,
    options: forges.filter((forge) => !forge.retired && (provider === undefined || forge.kind === provider))
      .map((forge) => ({ value: forge.forge_id, label: `${forge.slug} · ${forge.name}`, kind: forge.kind })),
  } : field);
}
