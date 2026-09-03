import { computed } from '@angular/core';
import {
  injectHubGardenProposalsQuery,
  injectHubWorkItemsQuery,
  type ProposalWorkItemVm,
  type WorkItemPointer,
  type WorkItemView,
} from 'fleet';

/** A finding id's own resolved work item, or `null` while no accepted-and-minted
 * proposal names it. */
export interface FindingWorkItemLookup {
  workItemFor(findingId: string): ProposalWorkItemVm | null;
}

/**
 * Resolves every finding's accepted-and-minted work item (D4) off the garden
 * proposals docket — split out of `gardening-findings-page.ts` purely to keep
 * that file under the lint's own line cap; every doc comment below is this page's
 * own reasoning, unchanged by the move.
 *
 * Every proposal's `findings` list maps its own finding ids to that proposal's own
 * `source`/`ref` pointer (`gardening-proposals-page.ts`'s own `acceptedItemPointer`
 * computed, generalized from one proposal to every accepted-and-minted proposal in
 * the docket at once), and every distinct pointer fans out through
 * `injectHubWorkItemsQuery` in one read rather than one per finding.
 */
export function injectFindingWorkItemLookup(): FindingWorkItemLookup {
  const proposalsQuery = injectHubGardenProposalsQuery();

  const findingWorkItemPointers = computed<ReadonlyMap<string, WorkItemPointer>>(() => {
    const map = new Map<string, WorkItemPointer>();
    for (const proposal of proposalsQuery.data() ?? []) {
      const closure = proposal.closure;
      if (closure?.closure !== 'accepted' || closure.item_outcome !== 'minted') continue;
      const pointer: WorkItemPointer = { source: closure.source!, ref: closure.ref! };
      for (const findingId of proposal.findings) map.set(findingId, pointer);
    }
    return map;
  });

  /** Every distinct pointer named above, deduplicated — `injectHubWorkItemsQuery`'s
   * own fan-out input, so two findings under the same proposal fan out to one read. */
  const workItemPointers = computed<readonly WorkItemPointer[]>(() => {
    const byKey = new Map<string, WorkItemPointer>();
    for (const pointer of findingWorkItemPointers().values()) byKey.set(`${pointer.source}:${pointer.ref}`, pointer);
    return Array.from(byKey.values());
  });

  const workItemsQuery = injectHubWorkItemsQuery(() => workItemPointers());

  const workItemsByPointerKey = computed<ReadonlyMap<string, WorkItemView>>(
    () => new Map((workItemsQuery.data() ?? []).map((item) => [`${item.source}:${item.ref}`, item])),
  );

  /** `label` falls back to the bare pointer, `gardening-proposals-page.ts`'s own
   * `workItemVm` fallback, since `WorkItemView.label` is itself nullable. */
  function workItemFor(findingId: string): ProposalWorkItemVm | null {
    const pointer = findingWorkItemPointers().get(findingId);
    if (pointer === undefined) return null;
    const item = workItemsByPointerKey().get(`${pointer.source}:${pointer.ref}`);
    if (item === undefined) return null;
    return { label: item.label ?? `${pointer.source}:${pointer.ref}`, webUrl: item.web_url ?? null };
  }

  return { workItemFor };
}
