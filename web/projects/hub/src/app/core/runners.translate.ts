import type { RunnerRegistryView } from 'fleet/shell';
import type { ProjectRunner } from './runners.domain';

export function toProjectRunner(runner: RunnerRegistryView): ProjectRunner {
  return {
    runner_id: runner.runner_id,
    runner_name: runner.runner_name,
    connection: runner.connection,
    online: runner.online,
    projects: (runner.projects ?? []).map(({ project_id, claimable }) => ({
      project_id,
      claimable,
    })),
  };
}
