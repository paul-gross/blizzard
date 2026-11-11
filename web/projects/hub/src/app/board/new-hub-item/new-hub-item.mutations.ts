import { createWorkItemApiWorkSourcesSourceItemsPost } from 'fleet';

export async function createHubItem(project: string, title: string, body: string, urgency: string): Promise<string> {
  const { data, error } = await createWorkItemApiWorkSourcesSourceItemsPost({
    path: { source: 'hub' }, query: { project }, body: { title, body, urgency }, throwOnError: false,
  });
  if (error) throw error;
  return data!.chunk_id;
}
