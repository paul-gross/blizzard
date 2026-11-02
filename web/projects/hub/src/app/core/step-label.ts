import type { RunnerBoardParts } from 'fleet';

/** A step label naming its graph — `adv-dwf/build` — or the bare label when the graph's name is unknown. Only a node
 * label takes the prefix: a status word standing in its place (`stopped`) stays bare. */
export function graphStepLabel(graphName: string | null | undefined, node: string, label: string = node): string {
  return graphName && label === node ? `${graphName}/${label}` : label;
}

/** A holding runner as the board's cards write it: `xps/r-claude`, or the bare name with no host. */
export function runnerBoardLabel(runner: RunnerBoardParts): string {
  return runner.host ? `${runner.host}/${runner.name}` : runner.name;
}

/** The separator between a card's step and its holding runner — the mobile card's own glyph. */
export const STEP_RUNNER_SEP = ' · ';
