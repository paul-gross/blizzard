import type { runnerApi } from 'fleet/runner-shell';

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
  /** Whether it can be chosen — a runner whose pages do not answer cannot. */
  readonly disabled: boolean;
  /** A runner's brake as a glyph beside its name — `pause` for its own, `stop` for the hub's — `null` while unbraked;
   * the brake's words, its reason among them, ride in {@link title}. */
  readonly brake: 'pause' | 'stop' | null;
  /** A project's colour as its swatch, `null` for All, a runner, or a project the hub gave none. */
  readonly color: string | null;
}

const ALL: FilterChoice = { value: null, label: 'All', title: 'every runner', disabled: false, brake: null, color: null };

/**
 * The Runner filter's choices: All, then every runner on the host in wire order, by name.
 *
 * A `current` filter naming no runner on the host — a stale link, or a runner since removed —
 * is offered last, disabled and titled as gone, so the chip strip shows why the panel is
 * empty and All stays one click away. Nothing is called gone while `pending` — the runner list
 * not yet landed.
 */
export function runnerFilterChoices(
  runners: readonly runnerApi.HostRunnerView[],
  current: string | null = null,
  pending = false,
): readonly FilterChoice[] {
  const gone = !pending && current !== null && !runners.some((runner) => runner.name === current);
  return [
    ALL,
    ...runners.map((runner) => {
      const routable = runner.served && runner.runner_id !== null;
      const words = routable ? brakeLabel(runner.pause) : null;
      return {
        value: runner.name,
        label: runner.name,
        title: runner.state !== 'serving' ? `${runner.name} is halted` : words === null ? runner.name : `${runner.name} · ${words}`,
        disabled: !routable,
        brake: words === null ? null : runner.pause?.hub ? ('stop' as const) : ('pause' as const),
        color: null,
      };
    }),
    ...(gone && current !== null
      ? [{ value: current, label: `${current} (gone)`, title: `${current} is no longer on this host`, disabled: true, brake: null, color: null }]
      : []),
  ];
}

/** The Project filter's choices: All, then every project the host serves, by name, each
 * carrying the colour its runner reports for it as a swatch. */
export function projectFilterChoices(projects: readonly PanelProject[]): readonly FilterChoice[] {
  return [
    { ...ALL, title: 'every project' },
    ...projects.map((project) => ({ value: project.slug, label: project.name, title: project.slug, disabled: false, brake: null, color: project.color })),
  ];
}
