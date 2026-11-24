import type { WorkItem } from 'fleet';

/** The work-item facts a board card uses to label and link its references. */
export interface BoardWorkItems {
  readonly items: readonly Pick<WorkItem, 'source' | 'ref' | 'title' | 'label' | 'web_url'>[];
}
