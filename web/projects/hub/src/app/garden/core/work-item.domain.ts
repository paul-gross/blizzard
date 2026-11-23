// bzh:frontend-domain-boundary — a hub work item as the garden reads it; no wire, no Angular.

/** A work item, named by its `source`/`ref` pointer. */
export interface WorkItem {
  readonly source: string;
  readonly ref: string;
  /** The item's label, when it carries one. */
  readonly label: string | null;
  /** `null` once the item's chunk is terminal. */
  readonly webUrl: string | null;
}
