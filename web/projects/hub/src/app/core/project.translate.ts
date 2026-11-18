import type { ProjectView } from 'fleet/shell';

import type { Project } from './project.domain';

export function fromProjectView(wire: ProjectView): Project {
  return {
    project_id: wire.project_id,
    slug: wire.slug,
    name: wire.name,
    description: wire.description,
    color: wire.color,
    revision: wire.revision,
    created_at: wire.created_at,
  };
}
