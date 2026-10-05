import type { WorkSourceSummary } from 'fleet';

import { factText, secretLinks } from '../config-links.model';
import { filterByLifecycle, type LifecycleFilter } from '../config-filter.model';
import type { ConfigBadgeVm, ConfigRowVm } from '../config-record-list';
import type { ConfigRecordVm } from '../config-record-panel';

/** A work source's badges — built-in, retired. */
export function workSourceBadges(source: WorkSourceSummary): readonly ConfigBadgeVm[] {
  const badges: ConfigBadgeVm[] = [];
  if (source.built_in) badges.push({ label: 'built-in', tone: 'spawning' });
  if (source.retired) badges.push({ label: 'retired', tone: 'idle' });
  return badges;
}

/** The work source list's rows under `filter`. */
export function workSourceRows(sources: readonly WorkSourceSummary[], filter: LifecycleFilter): readonly ConfigRowVm[] {
  return filterByLifecycle(sources, filter).map((source) => ({
    key: source.name,
    title: source.name,
    sub: [source.provider, source.locator, source.annotate ? 'annotates' : null].filter(
      (fact): fact is string => !!fact,
    ),
    badges: workSourceBadges(source),
    revision: source.revision ?? null,
    retired: source.retired ?? false,
  }));
}

/** A work source's detail — `null` with none loaded. The built-in source has no
 * fields of its own to show, no token, and no history. */
export function workSourceRecordVm(source: WorkSourceSummary | undefined): ConfigRecordVm | null {
  if (!source) return null;
  if (source.built_in) {
    return {
      name: source.name,
      badges: workSourceBadges(source),
      facts: [],
      revision: null,
      note: "The hub's own work source — always present, and not configurable.",
      links: null,
      hasHistory: false,
    };
  }
  return {
    name: source.name,
    badges: workSourceBadges(source),
    facts: [
      { label: 'Provider', value: factText(source.provider) },
      { label: 'Locator', value: factText(source.locator) },
      { label: 'API base', value: factText(source.api_base) },
      { label: 'Web base', value: factText(source.web_base) },
      { label: 'Annotates', value: source.annotate ? 'yes' : 'no' },
      { label: 'Edits items', value: source.edit ? 'yes' : 'no' },
      { label: 'Created by', value: factText(source.created_by) },
    ],
    revision: source.revision ?? null,
    note: source.retired ? 'Retired — no new items ingest from it.' : null,
    links: { heading: 'Token', links: secretLinks(source.secret), emptyText: 'No token secret.' },
    hasHistory: true,
  };
}
