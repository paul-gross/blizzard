import { compactRef, type FlowFactView, type FlowRecordPage, type FlowView, flowFactKindLabel, formatAbsolute, formatWhen, type WorkItemsView } from 'fleet';

import { laneTitle } from './delivery-band.model';

/** One fact of a flow's record as the dock lists it. */
export interface FlowRecordRow {
  readonly id: number;
  readonly kind: string;
  readonly kindLabel: string;
  readonly chunkId: string;
  readonly shortId: string;
  /** The chunk's first work item's title — `null` until the board's work-items read has it. */
  readonly title: string | null;
  readonly outcome: string | null;
  readonly when: string;
  readonly whenTitle: string;
}

/** Every fact the record's pages hold, newest first, as the dock lists them. */
export function flowRecordRows(
  pages: readonly FlowRecordPage[] | undefined,
  workItems: ReadonlyMap<string, WorkItemsView>,
  now: number,
): readonly FlowRecordRow[] {
  const at = new Date(now);
  return (pages ?? []).flatMap((page) => page.facts).map((fact: FlowFactView) => ({
    id: fact.id,
    kind: fact.kind,
    kindLabel: flowFactKindLabel(fact.kind),
    chunkId: fact.chunk_id,
    shortId: compactRef(fact.chunk_id),
    title: workItems.get(fact.chunk_id)?.items.find((item) => item.title)?.title ?? null,
    outcome: fact.outcome,
    when: formatWhen(fact.at, at),
    whenTitle: formatAbsolute(fact.at),
  }));
}

/** The record's heading: the flow's lane title once it is read, else its id. */
export function flowRecordTitle(flowId: string, flow: FlowView | undefined): string {
  return flow === undefined ? flowId : laneTitle(flow);
}
