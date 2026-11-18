/** Catalogue facts needed to link and display a repository on a project. */
export interface ProjectRepository {
  readonly repository_id: string;
  readonly slug: string;
  readonly name: string;
  readonly owner: string;
  readonly repo: string;
  readonly base_branch: string;
  readonly retired: boolean;
}
