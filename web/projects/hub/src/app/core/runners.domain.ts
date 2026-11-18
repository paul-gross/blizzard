/** Runner facts used by project presentation, including the hub's claimability judgment. */
export interface ProjectRunner {
  readonly runner_id: string;
  readonly runner_name: string;
  readonly connection: string;
  readonly online: boolean;
  readonly projects: readonly ProjectRunnerAssignment[];
}

export interface ProjectRunnerAssignment {
  readonly project_id: string | null;
  readonly claimable: boolean;
}

/** Live runners whose hub-resolved assignment can claim work for this project. */
export function servingProjectRunners(
  projectId: string,
  runners: readonly ProjectRunner[],
): readonly ProjectRunner[] {
  return runners.filter(
    (runner) =>
      runner.online &&
      runner.projects.some(
        (project) => project.project_id === projectId && project.claimable,
      ),
  );
}
