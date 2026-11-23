/** Authorship of a work item, as resolved by its source. */
export interface WorkItemAuthor {
  chunk_id: string | null;
  kind: string;
  login: string | null;
  node_name: string | null;
  runner_id: string | null;
  runner_name: string | null;
  user_id: string | null;
}

export type WorkItemPriority = { kind: 'low' | 'normal' | 'high' } | { kind: 'unknown'; raw: string };

/** One source item, including an individual fetch failure when present. */
export interface WorkItem {
  author: WorkItemAuthor | null;
  body: string | null;
  comments: string[];
  error: string | null;
  fetched_at: string;
  hub_source: boolean;
  label: string | null;
  ref: string;
  slug: string | null;
  source: string;
  stated_priority: WorkItemPriority | null;
  title: string | null;
  web_url: string | null;
}
