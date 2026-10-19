import type { runnerApi } from 'fleet';

import type { PanelProject } from '../../core/panel-reads.model';

/** A runner's brake in operator words — which side set it, and the local brake's reason
 * when it carries one — or `null` while neither brake is set. */
export function brakeLabel(pause: runnerApi.PauseStateView | null | undefined): string | null {
  if (!pause || (!pause.local && !pause.hub)) return null;
  if (pause.local && pause.hub) return 'paused here and by hub';
  if (pause.hub) return 'paused by hub';
  return pause.local_reason ? `paused: ${pause.local_reason}` : 'paused';
}

/** One choice a filter control offers: All (`value` null) or one runner or project. */
export interface FilterChoice {
  readonly value: string | null;
  readonly label: string;
  /** Its hover text. */
  readonly title: string;
  /** Whether it can be chosen — a halted runner cannot. */
  readonly disabled: boolean;
  /** A runner's brake as a glyph beside its name — `pause` for its own, `stop` for the hub's — `null` while unbraked;
   * the brake's words, its reason among them, ride in {@link title}. */
  readonly brake: 'pause' | 'stop' | null;
}

const ALL: FilterChoice = { value: null, label: 'All', title: 'every runner', disabled: false, brake: null };

/** The Runner filter's choices: All, then every runner on the host in wire order, by name. */
export function runnerFilterChoices(runners: readonly runnerApi.HostRunnerView[]): readonly FilterChoice[] {
  return [
    ALL,
    ...runners.map((runner) => {
      const serving = runner.state === 'serving' && runner.runner_id !== null;
      const words = serving ? brakeLabel(runner.pause) : null;
      return {
        value: runner.name,
        label: runner.name,
        title: serving ? (words === null ? runner.name : `${runner.name} · ${words}`) : `${runner.name} is halted`,
        disabled: !serving,
        brake: words === null ? null : runner.pause?.hub ? ('stop' as const) : ('pause' as const),
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
