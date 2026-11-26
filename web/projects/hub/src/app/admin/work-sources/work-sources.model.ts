import type { ViewportMode } from 'fleet/viewport';

import { builtInTone, lifecycleTone } from '../../core/lifecycle-tone';
import type { ConfigFieldOption } from '../config-edit.model';
import type { ConfigFieldView } from '../config-field-tree.model';
import { configActions, type ConfigActionsVm } from '../config-actions.model';
import { credentialLinks, factText, recordLink } from '../config-links.model';
import { filterByLifecycle, type LifecycleFilter } from '../config-filter.model';
import type { ConfigBadgeVm, ConfigRowVm } from '../config-record-list';
import type { ConfigRecordVm } from '../config-record-panel';
import type { WorkSource, WorkSourceDraft } from './work-source.domain';
import { workSourceForgeRef } from './work-source.translate';

/** A work source's badges — built-in, retired. */
export function workSourceBadges(source: WorkSource): readonly ConfigBadgeVm[] {
  const badges: ConfigBadgeVm[] = [];
  if (source.builtIn) badges.push({ label: 'built-in', tone: builtInTone });
  if (source.retired) badges.push({ label: 'retired', tone: lifecycleTone(true) });
  return badges;
}

/** What a work source is addressed by — its id, or `hub` for the built-in source, which has none. */
export function workSourceKey(source: WorkSource): string {
  return source.id ?? source.name;
}

/** The handle a work source shows — its current slug. */
export function workSourceSlug(source: WorkSource): string {
  return source.slug ?? source.name;
}

/** What the CLI change verb names a work source by — its slug; none for the built-in source, which takes no change. */
export function workSourceCliName(source: WorkSource | undefined): string | null {
  return source && !source.builtIn ? workSourceSlug(source) : null;
}

/** The work source list's rows under `filter`. */
export function workSourceRows(sources: readonly WorkSource[], filter: LifecycleFilter): readonly ConfigRowVm[] {
  return filterByLifecycle(sources, filter).map((source) => ({
    key: workSourceKey(source),
    title: workSourceSlug(source),
    sub: [
      source.name === workSourceSlug(source) ? null : source.name,
      source.provider, source.locator, source.annotate ? 'annotates' : null].filter(
      (fact): fact is string => !!fact,
    ),
    badges: workSourceBadges(source),
    revision: source.revision,
    retired: source.retired,
  }));
}

/** A work source's detail — `null` with none loaded. The built-in source has no
 * fields of its own to show, no token, and no history. */
export function workSourceRecordVm(source: WorkSource | undefined): ConfigRecordVm | null {
  if (!source) return null;
  if (source.builtIn) {
    return {
      name: workSourceSlug(source),
      badges: workSourceBadges(source),
      facts: [],
      revision: null,
      note: "The hub's own work source — always present, and not configurable.",
      links: null,
      hasHistory: false,
    };
  }
  return {
    name: workSourceSlug(source),
    badges: workSourceBadges(source),
    facts: [
      { label: 'Name', value: factText(source.name) },
      { label: 'Provider', value: factText(source.provider) },
      { label: 'Locator', value: factText(source.locator) },
      { label: 'Forge', value: factText(source.forgeName ?? source.forgeSlug) },
      { label: 'Annotates', value: source.annotate ? 'yes' : 'no' },
      { label: 'Edits items', value: source.edit ? 'yes' : 'no' },
      { label: 'Created by', value: factText(source.createdByName ?? source.createdBy) },
    ],
    revision: source.revision,
    note: source.retired ? 'Retired — no new items ingest from it.' : null,
    links: {
       heading: 'Related records',
       links: [recordLink(workSourceForgeRef(source)), ...credentialLinks(source.credentialId, source.credentialSlug)],
       emptyText: 'No related records.',
    },
    hasHistory: true,
  };
}

/** The write controls a work source offers: none for the built-in source. */
export function workSourceActions(
  mode: ViewportMode,
  canEdit: boolean,
  source: WorkSource | undefined,
): ConfigActionsVm | null {
  return configActions(mode, canEdit, source && { built_in: source.builtIn, retired: source.retired });
}

/** A work source's form fields, presentation only: the rules say which are required. The slug is the handle people
 * type and may be renamed; the name is its display label, and left blank on create it takes the slug. The forge is one
 * whose kind matches the provider, and the credential one of that forge's. */
export function workSourceFieldViews(
  forges: readonly ConfigFieldOption[],
  credentials: readonly ConfigFieldOption[],
): readonly ConfigFieldView<WorkSourceDraft>[] {
  return [
    { key: 'slug', label: 'Slug', kind: 'text' },
    { key: 'name', label: 'Name', kind: 'text' },
    { key: 'provider', label: 'Provider', kind: 'text' },
    { key: 'locator', label: 'Locator', kind: 'text' },
    { key: 'forge', label: 'Forge', kind: 'select', options: forges, filterBy: 'provider' },
    { key: 'credential', label: 'Credential', kind: 'select', options: credentials, filterBy: 'forge' },
    { key: 'annotate', label: 'Annotates', kind: 'checkbox' },
  ];
}
