import { type BeaconTone, type runnerApi } from 'fleet';

import type { RunnerDashboard } from '../../core/panel-reads.model';

/** One binding of a runner: the project it serves in one workspace, and its agents in use of those it declares. */
export interface HostPanelBinding {
  readonly key: string;
  /** The project entry, with the workspace beside it only when the runner binds more than one workspace. */
  readonly label: string;
  readonly used: number;
  readonly agents: number;
}

/** One runner on the host panel: its light, its bindings' agents in use, and its brakes. */
export interface HostPanelRunner {
  readonly key: string;
  readonly runnerId: string | null;
  readonly name: string;
  /** Red for a halted runner, yellow while either brake holds it, else green. */
  readonly tone: BeaconTone;
  readonly halted: boolean;
  readonly haltedReasons: readonly string[];
  readonly agentsInUse: number;
  readonly maxAgents: number;
  /** One line per binding, in declaration order — empty for a runner that declares none, which shows its one total. */
  readonly bindings: readonly HostPanelBinding[];
  /** The runner's own brake, worded for its glyph's tooltip; `null` while it is off. */
  readonly localPause: string | null;
  readonly hubPaused: boolean;
}

/** A runner's brake in operator words for its glyph, or `null` while its own brake is off. */
function localPauseText(
  pause: runnerApi.PauseStateView | null | undefined,
): string | null {
  if (!pause?.local) return null;
  return pause.local_reason
    ? `Paused here: ${pause.local_reason}`
    : 'Paused here — no new claims';
}

/** A runner's binding lines: each binding the host declares for it, its agents in use read off the runner's own
 * slot accounting (`capacities`, `undefined` until that read lands, when every binding reads 0 in use). */
function bindingLines(
  bindings: readonly runnerApi.HostRunnerBindingView[],
  capacities: runnerApi.CapacitiesView | undefined,
): readonly HostPanelBinding[] {
  const manyWorkspaces =
    new Set(bindings.map((binding) => binding.workspace_id)).size > 1;
  return bindings.map((binding) => {
    const used = capacities?.bindings?.find(
      (capacity) =>
        capacity.project === binding.project &&
        capacity.workspace_id === binding.workspace_id,
    )?.used;
    return {
      key: `${binding.project}@${binding.workspace_id}`,
      label: manyWorkspaces
        ? `${binding.project} · ${binding.workspace_id}`
        : binding.project,
      used: used ?? 0,
      agents: binding.agents,
    };
  });
}

/** One {@link HostPanelRunner} per runner the host serves or holds halted, in wire order, each serving runner's bindings
 * filled from its slot accounting in `capacities` (by runner id). */
export function hostPanelRunners(
  runners: readonly runnerApi.HostRunnerView[],
  capacities: ReadonlyMap<string, runnerApi.CapacitiesView>,
): readonly HostPanelRunner[] {
  return runners.map((runner, index) => {
    const halted = runner.state !== 'serving';
    const localPause = halted ? null : localPauseText(runner.pause);
    const hubPaused = !halted && (runner.pause?.hub ?? false);
    return {
      key: runner.runner_id ?? `${runner.name}#${index}`,
      runnerId: runner.runner_id,
      name: runner.name,
      tone: halted
        ? 'red'
        : localPause !== null || hubPaused
          ? 'amber'
          : 'green',
      halted,
      haltedReasons: halted ? runner.halted_reasons : [],
      agentsInUse: runner.agents_in_use,
      maxAgents: runner.max_agents,
      bindings: halted
        ? []
        : bindingLines(
            runner.bindings,
            capacities.get(runner.runner_id ?? ''),
          ),
      localPause,
      hubPaused,
    };
  });
}

/** `runners` narrowed to the one named `name` on the host, every one for `null`. */
export function runnersNamed(
  runners: readonly runnerApi.HostRunnerView[],
  name: string | null,
): readonly runnerApi.HostRunnerView[] {
  return name === null ? runners : runners.filter((runner) => runner.name === name);
}

/** Each runner's own slot accounting by runner id, off its dashboard. */
export function capacitiesByRunner(
  dashboards: readonly RunnerDashboard[],
): ReadonlyMap<string, runnerApi.CapacitiesView> {
  return new Map(
    dashboards.map(
      (entry) => [entry.owner.runnerId, entry.dashboard.runner.capacities] as const,
    ),
  );
}
