import { readRefusal, type SaveOutcome } from 'fleet';

import {
  injectConfirmGoneFindingsMutation,
  injectNotAFindingFindingsMutation,
  injectReopenFindingsMutation,
  injectResolveFindingsMutation,
  injectSupersedeFindingsMutation,
  injectWontFixFindingsMutation,
} from '../../core/finding.mutations';
import type { FindingTriageDraft } from './finding-triage.domain';
import { FINDING_TRIAGE_REFUSALS, toExitVars, toSupersedeVars } from './finding-triage.translate';

export interface FindingTriageService {
  /** Triages the findings under the draft's verb. Resolves only after the finding caches refetch, and answers every
   * refusal as a `SaveOutcome` rather than throwing it. */
  triage(findingIds: readonly string[], draft: FindingTriageDraft): Promise<SaveOutcome<FindingTriageDraft>>;
}

/** Drives the six finding verbs' existing mutations. */
export function injectFindingTriageService(): FindingTriageService {
  const resolve = injectResolveFindingsMutation();
  const confirmGone = injectConfirmGoneFindingsMutation();
  const wontFix = injectWontFixFindingsMutation();
  const notAFinding = injectNotAFindingFindingsMutation();
  const supersede = injectSupersedeFindingsMutation();
  const reopen = injectReopenFindingsMutation();

  const run = (findingIds: readonly string[], draft: FindingTriageDraft): Promise<unknown> => {
    switch (draft.verb) {
      case 'resolve':
        return resolve.mutateAsync(toExitVars(findingIds, draft));
      case 'confirm-gone':
        return confirmGone.mutateAsync(toExitVars(findingIds, draft));
      case 'wont-fix':
        return wontFix.mutateAsync(toExitVars(findingIds, draft));
      case 'not-a-finding':
        return notAFinding.mutateAsync(toExitVars(findingIds, draft));
      case 'supersede':
        return supersede.mutateAsync(toSupersedeVars(findingIds, draft));
      case 'reopen':
        return reopen.mutateAsync(toExitVars(findingIds, draft));
      default:
        return Promise.reject(new Error(`Unknown triage verb ${String(draft.verb)}.`));
    }
  };

  return {
    async triage(findingIds, draft) {
      try {
        await run(findingIds, draft);
        return { ok: true };
      } catch (error) {
        return readRefusal(error, FINDING_TRIAGE_REFUSALS, `${draft.verb} failed.`);
      }
    },
  };
}
