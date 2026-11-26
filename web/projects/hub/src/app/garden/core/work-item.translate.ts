// bzh:frontend-domain-boundary — the one place the work item wire is read for the garden.
import type { WorkItemView } from 'fleet/hub-api';

import type { WorkItem } from './work-item.domain';

/** A work item as the hub serves it, read into the domain. */
export function fromWorkItemView(wire: WorkItemView): WorkItem {
  return { source: wire.source, ref: wire.ref, label: wire.label ?? null, webUrl: wire.web_url ?? null };
}
