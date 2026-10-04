/** A pause/resume request in flight — the requested hub brake for one runner. */
export interface PendingRunnerPause {
  readonly runnerId: string;
  readonly paused: boolean;
}

/** The runner ids a pause/resume is currently in flight for — the per-row disable each toggle checks. */
export function pendingRunnerIds(vars: readonly PendingRunnerPause[]): readonly string[] {
  return vars.map((pause) => pause.runnerId);
}

/**
 * `rows`, with each in-flight pause/resume's requested `hub_paused` folded onto its row. The override is total:
 * the request names the brake's value outright. Returns `rows` itself with nothing in flight.
 */
export function withPendingRunnerPauses<Row extends { readonly runner_id: string; readonly hub_paused: boolean }>(
  rows: readonly Row[],
  vars: readonly PendingRunnerPause[],
): readonly Row[] {
  if (vars.length === 0) return rows;
  const requested = new Map(vars.map((pause) => [pause.runnerId, pause.paused]));
  return rows.map((row) => {
    const override = requested.get(row.runner_id);
    return override === undefined ? row : { ...row, hub_paused: override };
  });
}
