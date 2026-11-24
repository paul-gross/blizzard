import type { WorkItemsView } from '../../api/hub';
import type { WorkItem, WorkItemPriority } from '../chunk-detail/work-item.model';

function priority(raw: string): WorkItemPriority {
  switch (raw) {
    case 'low':
    case 'normal':
    case 'high':
      return { kind: raw };
    default:
      return { kind: 'unknown', raw };
  }
}

/** Translate the forge read without exposing its transport objects to presentation. */
export function fromWorkItemsView(wire: WorkItemsView): { items: WorkItem[] } {
  return {
    items: wire.items.map((item) => ({
      author: item.author === null ? null : {
        chunk_id: item.author.chunk_id,
        kind: item.author.kind,
        login: item.author.login,
        node_name: item.author.node_name,
        runner_id: item.author.runner_id,
        runner_name: item.author.runner_name,
        user_id: item.author.user_id,
      },
      body: item.body,
      comments: [...item.comments],
      error: item.error,
      fetched_at: item.fetched_at,
      hub_source: item.hub_source,
      label: item.label,
      ref: item.ref,
      slug: item.slug,
      source: item.source,
      stated_priority: item.stated_priority === null ? null : priority(item.stated_priority),
      title: item.title,
      web_url: item.web_url,
    })),
  };
}
