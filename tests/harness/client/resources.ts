/** A resource tests create: the create calls that return one and where its id sits, and the list calls and where their items sit. */
export interface Resource {
  creates: string[];
  /** The field of a created item, and of a list's items, that holds the id. */
  id: string;
  /** `'GET <route>'` to the field of the answer holding the list. */
  lists: Record<string, string>;
}

/** The map holds only the resources tests create, and grows when a port creates a new kind. */
export const RESOURCES: Record<string, Resource> = {
  chunk: { creates: ['POST /api/chunks'], id: 'chunk_id', lists: { 'GET /api/chunks': 'chunks' } },
  project: { creates: ['POST /api/projects'], id: 'project_id', lists: { 'GET /api/projects': 'projects' } },
};
