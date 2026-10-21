import type { RunnerRegistryView, ServedProjectView } from 'fleet';

/** The projects a runner declares it serves, as the hub resolved each entry; empty when it declares none. */
export function servedProjects(runner: RunnerRegistryView): readonly ServedProjectView[] {
  return runner.projects ?? [];
}

/** Whether `runner` declares the project `projectId` among the projects it serves. */
export function runnerServes(runner: RunnerRegistryView, projectId: string): boolean {
  return servedProjects(runner).some((served) => served.project_id === projectId);
}

/** Whether `runner` is unassigned — it declares no project, so it is offered no work. */
export function isUnassigned(runner: RunnerRegistryView): boolean {
  return servedProjects(runner).length === 0;
}

/** The entries of `runner`'s declaration that name no project of the tenant, as the runner wrote them. */
export function unresolvedEntries(runner: RunnerRegistryView): readonly string[] {
  return servedProjects(runner)
    .filter((served) => served.project_id == null)
    .map((served) => served.entry);
}

/**
 * The live runners that can take a claim on the project `projectId`: online by the hub's own clock, and with the
 * hub's judgment (`claimable`) that its claim path would take the project — which a paused, retired or
 * version-refused runner, or one reached only through withheld bindings, is not.
 * An empty answer is a project no live runner serves — its work waits until one does.
 */
export function servingRunners(
  projectId: string,
  runners: readonly RunnerRegistryView[],
): readonly RunnerRegistryView[] {
  return runners.filter(
    (runner) =>
      runner.online && servedProjects(runner).some((served) => served.project_id === projectId && served.claimable),
  );
}
