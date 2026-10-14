import type { runnerApi } from 'fleet';

import { brakeLabel } from '../../host/host-runners.model';

/** One runner the switcher offers. */
export interface RunnerChoice {
  /** The id its pages are addressed under; `null` for a halted runner with none. */
  readonly runnerId: string | null;
  readonly name: string;
  /** Its brake in operator words, `null` while unbraked. */
  readonly brake: string | null;
  /** Whether it has pages to open — a halted runner has none. */
  readonly openable: boolean;
}

/** The switcher's choices — every runner on the host, in wire order. */
export function runnerChoices(runners: readonly runnerApi.HostRunnerView[]): readonly RunnerChoice[] {
  return runners.map((runner) => {
    const openable = runner.state === 'serving' && runner.runner_id !== null;
    return {
      runnerId: runner.runner_id,
      name: runner.name,
      brake: openable ? brakeLabel(runner.pause) : null,
      openable,
    };
  });
}
