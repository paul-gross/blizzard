/**
 * The TanStack Query mutation keys the fleet's mutations carry, in one place — mirrors
 * `query-keys.ts`'s own `hub`-namespaced flat-constant shape, with a `mutation` segment
 * so a mutation key is never mistaken for a query key sharing the same namespace.
 */
export const promoteChunkMutationKey = ['hub', 'mutation', 'promote-chunk'] as const;
export const repositionQueueMutationKey = ['hub', 'mutation', 'reposition-queue'] as const;
export const repositionBacklogMutationKey = ['hub', 'mutation', 'reposition-backlog'] as const;
export const runnerPauseMutationKey = ['hub', 'mutation', 'runner-pause'] as const;
export const chunkPauseMutationKey = ['hub', 'mutation', 'chunk-pause'] as const;
export const graphLifecycleMutationKey = ['hub', 'mutation', 'graph-lifecycle'] as const;
export const chunkDetachMutationKey = ['hub', 'mutation', 'chunk-detach'] as const;
export const chunkCompleteMutationKey = ['hub', 'mutation', 'chunk-complete'] as const;
export const chunkDeleteMutationKey = ['hub', 'mutation', 'chunk-delete'] as const;
export const chunkSetGraphMutationKey = ['hub', 'mutation', 'chunk-set-graph'] as const;
export const answerQuestionMutationKey = ['hub', 'mutation', 'answer-question'] as const;
export const resolveDecisionMutationKey = ['hub', 'mutation', 'resolve-decision'] as const;
export const scopeLifecycleMutationKey = ['hub', 'mutation', 'scope-lifecycle'] as const;
export const editScopeMutationKey = ['hub', 'mutation', 'edit-scope'] as const;
export const runRoutineMutationKey = ['hub', 'mutation', 'run-routine'] as const;
export const resolveFindingsMutationKey = ['hub', 'mutation', 'resolve-findings'] as const;
export const confirmGoneFindingsMutationKey = ['hub', 'mutation', 'confirm-gone-findings'] as const;
export const wontFixFindingsMutationKey = ['hub', 'mutation', 'wont-fix-findings'] as const;
export const notAFindingFindingsMutationKey = ['hub', 'mutation', 'not-a-finding-findings'] as const;
export const supersedeFindingsMutationKey = ['hub', 'mutation', 'supersede-findings'] as const;
export const reopenFindingsMutationKey = ['hub', 'mutation', 'reopen-findings'] as const;
export const acceptGardenProposalMutationKey = ['hub', 'mutation', 'accept-garden-proposal'] as const;
export const passGardenProposalMutationKey = ['hub', 'mutation', 'pass-garden-proposal'] as const;
export const logoutMutationKey = ['hub', 'mutation', 'logout'] as const;
export const assignRoleMutationKey = ['hub', 'mutation', 'assign-role'] as const;
