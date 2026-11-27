import { computed, type Signal } from '@angular/core';
import { readRefusal } from 'fleet/core';
import { type SaveOutcome } from 'fleet/forms';

import type { RunDraft, RunResponse } from '../core/run.domain';
import { fromRoutineRunResponse, RUN_REFUSALS, toRunRequest } from '../core/run.translate';
import { injectRunRoutineMutation } from './routine-run.mutations';

/** How a run ended: the run the hub minted, or a refusal answered per draft field. */
export type RunOutcome = { readonly ok: true; readonly run: RunResponse } | Exclude<SaveOutcome<RunDraft>, { ok: true }>;

/** Runs a routine from a draft: domain in, a {@link RunOutcome} out. */
export interface RunService {
  /** Whether a run is in flight. */
  readonly pending: Signal<boolean>;
  run(routineId: string, draft: RunDraft): Promise<RunOutcome>;
}

/**
 * The run service over the run mutation. A run resolves once the mutation's invalidations settle
 * (`bzh:frontend-mutation-settles-on-refresh`); a refusal is answered, never thrown: a field refusal on its field,
 * anything else on the form line. Nothing is retried.
 */
export function injectRunService(): RunService {
  const mutation = injectRunRoutineMutation();
  return {
    pending: computed(() => mutation.isPending()),
    async run(routineId, draft) {
      try {
        const run = await mutation.mutateAsync({ routineId, ...toRunRequest(draft) });
        return { ok: true, run: fromRoutineRunResponse(run) };
      } catch (error) {
        return readRefusal(error, RUN_REFUSALS, 'Run failed.') as Exclude<RunOutcome, { ok: true }>;
      }
    },
  };
}
