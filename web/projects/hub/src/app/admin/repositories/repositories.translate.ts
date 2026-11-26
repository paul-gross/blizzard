import type { RepositorySummary } from 'fleet/hub-api';
import type { ProjectRepository } from './repositories.domain';

export function toProjectRepository(
  repository: RepositorySummary,
): ProjectRepository {
  const { repository_id, slug, name, owner, repo, base_branch, retired } =
    repository;
  return { repository_id, slug, name, owner, repo, base_branch, retired };
}
