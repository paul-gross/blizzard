import type { WorkItemCreateResponse } from 'fleet';

export const CREATED_ITEM: WorkItemCreateResponse = {
  author: { kind: 'user', user_id: 'operator', login: 'operator', chunk_id: null, node_name: null, runner_id: null, runner_name: null },
  body: 'Body', chunk_id: 'ch_created', closed_at: null, closure: null,
  created_at: '2026-01-01T00:00:00Z', edited_at: '2026-01-01T00:00:00Z',
  label: 'hub:1', ref: '1', slug: 'hub', source: 'hub', stated_priority: null, title: 'Title', web_url: null,
};
