import type { runnerApi } from 'fleet';

/** One runner as the machine page lists it — every value resolved for display. */
export interface HostRunnerRow {
  /** The hub-minted id, `null` for a halted runner configured without one. */
  readonly runnerId: string | null;
  readonly name: string;
  readonly serving: boolean;
  /** The runner's brake in operator words, `null` while it runs unbraked. */
  readonly brake: string | null;
  /** Agents in use of the runner's ceiling, `null` for a halted runner. */
  readonly agents: string | null;
  /** One line per binding: its project, workspace, and agent ceiling there. */
  readonly bindings: readonly string[];
  readonly haltedReasons: readonly string[];
  /** The name the board's Runner filter narrows to it by, `null` where there is no runner to
   * read — not served, or no id. A runner its hub halts is still served, so it keeps its link. */
  readonly boardFilter: string | null;
}

/** A runner's brake in operator words — which side set it, and the local brake's reason
 * when it carries one — or `null` while neither brake is set. */
export function brakeLabel(pause: runnerApi.PauseStateView | null | undefined): string | null {
  if (!pause || (!pause.local && !pause.hub)) return null;
  if (pause.local && pause.hub) return 'paused here and by hub';
  if (pause.hub) return 'paused by hub';
  return pause.local_reason ? `paused: ${pause.local_reason}` : 'paused';
}

/** One {@link HostRunnerRow} per runner, in wire order. */
export function hostRunnerRows(runners: readonly runnerApi.HostRunnerView[]): readonly HostRunnerRow[] {
  return runners.map((runner) => {
    const serving = runner.state === 'serving';
    const served = runner.served ?? serving;
    return {
      runnerId: runner.runner_id,
      name: runner.name,
      serving,
      brake: serving ? brakeLabel(runner.pause) : null,
      agents: serving ? `${runner.agents_in_use ?? 0}/${runner.max_agents ?? 0} agents` : null,
      bindings: (runner.bindings ?? []).map(
        (binding) => `${binding.project} · ${binding.workspace_id} · ${binding.agents} agents`,
      ),
      haltedReasons: serving ? [] : (runner.halted_reasons ?? []),
      boardFilter: served && runner.runner_id !== null ? runner.name : null,
    };
  });
}
