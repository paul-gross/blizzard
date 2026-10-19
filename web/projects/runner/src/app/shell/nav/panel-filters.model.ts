import type { runnerApi } from 'fleet';

import type { PanelProject } from '../../core/panel-reads.model';
import { brakeLabel } from '../../host/host-runners.model';

/** One choice a filter control offers: All (`value` null) or one runner or project. */
export interface FilterChoice {
  readonly value: string | null;
  readonly label: string;
  /** Its hover text. */
  readonly title: string;
  /** Whether it can be chosen — a halted runner cannot. */
  readonly disabled: boolean;
  /** A runner's brake in operator words, `null` while unbraked. */
  readonly brake: string | null;
}

const ALL: FilterChoice = { value: null, label: 'All', title: 'every runner', disabled: false, brake: null };

/** The Runner filter's choices: All, then every runner on the host in wire order, by name. */
export function runnerFilterChoices(runners: readonly runnerApi.HostRunnerView[]): readonly FilterChoice[] {
  return [
    ALL,
    ...runners.map((runner) => {
      const serving = runner.state === 'serving' && runner.runner_id !== null;
      const brake = serving ? brakeLabel(runner.pause) : null;
      return {
        value: runner.name,
        label: runner.name,
        title: serving ? (brake ?? runner.name) : `${runner.name} is halted`,
        disabled: !serving,
        brake,
      };
    }),
  ];
}

/** The Project filter's choices: All, then every project the host serves, by name. The
 * runner reports no project color, so no choice carries a swatch. */
export function projectFilterChoices(projects: readonly PanelProject[]): readonly FilterChoice[] {
  return [
    { ...ALL, title: 'every project' },
    ...projects.map((project) => ({ value: project.slug, label: project.name, title: project.slug, disabled: false, brake: null })),
  ];
}
