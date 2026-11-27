import type { EditBaseline } from 'fleet/forms';
import type { ProjectRepositoryLink } from './project-read.domain';

/** Blank is an invalid override, not an instruction to inherit. */
export interface ProjectBranchDraft { baseBranch: string }
export interface ProjectBranchBaseline extends EditBaseline<ProjectBranchDraft> {
  readonly projectId: string;
  readonly override: string | null;
}

export function projectBranchBaseline(link: ProjectRepositoryLink): ProjectBranchBaseline {
  return { id: link.repository_id, projectId: link.project_id, revision: link.revision,
    override: link.base_branch, draft: { baseBranch: link.base_branch ?? '' } };
}

export function projectBranchError(name: string): string | null {
  if (!name || /[\s\u0085]/u.test(name) || [...name].some((char) => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127) ||
      /[~^:?*[\\]/u.test(name) || name.includes('..') || name.includes('@{') ||
      name.startsWith('-') || name.startsWith('/') || name.endsWith('/') || name.includes('//') ||
      name.endsWith('.') || name.split('/').some((part) => part.startsWith('.') || part.endsWith('.lock')) ||
      name === '@' || name === 'HEAD') return 'Enter a valid git branch name.';
  return null;
}
