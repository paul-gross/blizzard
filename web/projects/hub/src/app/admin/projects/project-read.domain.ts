/** A standing source link, including unlinked and built-in entries. */
export interface ProjectSourceLink {
  readonly project_id: string;
  readonly name: string;
  readonly slug: string | null;
  readonly linked: boolean;
  readonly built_in: boolean;
  readonly revision: number | null;
}

/** A standing repository link in the hub's landing order. */
export interface ProjectRepositoryLink {
  readonly project_id: string;
  readonly repository_id: string;
  readonly name: string;
  readonly slug: string;
  readonly linked: boolean;
  readonly revision: number;
  readonly position: number | null;
  readonly base_branch: string | null;
  readonly effective_base_branch: string;
}

export interface ProjectChunkCounts {
  readonly delivering: number;
  readonly done: number;
  readonly done_since: number | null;
  readonly needs_human: number;
  readonly not_ready: number;
  readonly paused: number;
  readonly ready: number;
  readonly running: number;
  readonly stopped: number;
  readonly terminal: number;
  readonly total: number;
  readonly waiting_on_human: number;
}

/** Each read is absent until the project's figures have been fetched. */
export interface ProjectFigures {
  readonly sourceLinks?: readonly ProjectSourceLink[];
  readonly repositoryLinks?: readonly ProjectRepositoryLink[];
}
