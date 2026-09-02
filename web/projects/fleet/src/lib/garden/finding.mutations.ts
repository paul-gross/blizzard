import { inject } from '@angular/core';
import { QueryClient, injectMutation } from '@tanstack/angular-query-experimental';

import {
  confirmGoneFindingsApiFindingsConfirmGonePost,
  notAFindingFindingsApiFindingsNotAFindingPost,
  reopenFindingsApiFindingsReopenPost,
  resolveFindingsApiFindingsResolvePost,
  supersedeFindingsApiFindingsSupersedePost,
  wontFixFindingsApiFindingsWontFixPost,
  type FindingView,
} from '../api/hub';
import { hubFindingsBucketPrefixKey, hubFindingsKey } from '../query-keys';

/** `POST /api/findings/{verb}` — the shared vars shape every human-driven exit and
 * `reopen` take (`FindingExitRequest`'s own D7 note: every finding named exits, or
 * reopens, together, one call, carrying the same required note). */
export interface FindingExitVars {
  readonly findingIds: readonly string[];
  readonly note: string;
}

/** `POST /api/findings/supersede` — {@link FindingExitVars} plus the absorbing
 * finding, `FindingSupersedeRequest`'s own D4 shape. */
export interface FindingSupersedeVars extends FindingExitVars {
  readonly supersededBy: string;
}

/** Both surfaces that cache a finding's own record — the triage bucket
 * ({@link hubFindingsBucketPrefixKey}) and the docket detail's evidence table
 * ({@link hubFindingsKey}, which caches the same findings under its own by-id key,
 * `finding.query.ts`'s own `injectHubFindingsQuery`) — invalidated together on every
 * exit/reopen so neither is left showing a finding's stale state. Findings carry no
 * SSE event of their own (`hubGardenProposalsKey`'s own standing), so this is direct
 * invalidation only, `garden-proposal.mutations.ts`'s own shape. A mutation doesn't
 * know which routine/scope is currently selected, so it invalidates the bucket's bare
 * key prefix rather than one selection's own key (`hubFindingsBucketPrefixKey`'s own
 * doc comment); it likewise invalidates `hubFindingsKey`'s bare prefix rather than one
 * proposal's own id list, since it doesn't know which ids the currently-open docket
 * detail is reading either. */
function invalidateFindingCaches(queryClient: QueryClient): void {
  void queryClient.invalidateQueries({ queryKey: hubFindingsBucketPrefixKey });
  void queryClient.invalidateQueries({ queryKey: hubFindingsKey([]) });
}

/**
 * Records that the work answering a finding landed — `POST /api/findings/resolve`.
 * 404 for an unknown id, 422 for a blank note. A hand resolution names no garden
 * proposal — that attribution is Phase 3's own, delivery-triggered.
 * {@link invalidateFindingCaches}'s own direct-invalidation shape.
 */
export function injectResolveFindingsMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: FindingExitVars): Promise<FindingView[]> => {
      const { data, error } = await resolveFindingsApiFindingsResolvePost({
        body: { finding_ids: [...vars.findingIds], note: vars.note },
        throwOnError: false,
      });
      if (error) throw error;
      return data!;
    },
    onSuccess: () => invalidateFindingCaches(queryClient),
  }));
}

/**
 * Confirms by hand that every named finding no longer reproduces — `POST
 * /api/findings/confirm-gone`. 404 for an unknown id, 422 for a blank note.
 * {@link invalidateFindingCaches}'s own direct-invalidation shape.
 */
export function injectConfirmGoneFindingsMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: FindingExitVars): Promise<FindingView[]> => {
      const { data, error } = await confirmGoneFindingsApiFindingsConfirmGonePost({
        body: { finding_ids: [...vars.findingIds], note: vars.note },
        throwOnError: false,
      });
      if (error) throw error;
      return data!;
    },
    onSuccess: () => invalidateFindingCaches(queryClient),
  }));
}

/**
 * Withdraws every named finding as won't-fix — the ground hasn't moved, a person has
 * decided it doesn't merit standing regardless — `POST /api/findings/wont-fix`. 404
 * for an unknown id, 422 for a blank note. {@link invalidateFindingCaches}'s own
 * direct-invalidation shape.
 */
export function injectWontFixFindingsMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: FindingExitVars): Promise<FindingView[]> => {
      const { data, error } = await wontFixFindingsApiFindingsWontFixPost({
        body: { finding_ids: [...vars.findingIds], note: vars.note },
        throwOnError: false,
      });
      if (error) throw error;
      return data!;
    },
    onSuccess: () => invalidateFindingCaches(queryClient),
  }));
}

/**
 * Withdraws every named finding as not a finding at all — `POST
 * /api/findings/not-a-finding`. 404 for an unknown id, 422 for a blank note.
 * {@link invalidateFindingCaches}'s own direct-invalidation shape.
 */
export function injectNotAFindingFindingsMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: FindingExitVars): Promise<FindingView[]> => {
      const { data, error } = await notAFindingFindingsApiFindingsNotAFindingPost({
        body: { finding_ids: [...vars.findingIds], note: vars.note },
        throwOnError: false,
      });
      if (error) throw error;
      return data!;
    },
    onSuccess: () => invalidateFindingCaches(queryClient),
  }));
}

/**
 * Withdraws every named finding as superseded by `supersededBy` — `POST
 * /api/findings/supersede`. 404 for an unknown id in either the named findings or
 * `supersededBy`, 422 for a blank note, a self-superseding id, or a `supersededBy`
 * that isn't itself live (`FindingSupersedeRequest`'s own D4 shape).
 * {@link invalidateFindingCaches}'s own direct-invalidation shape.
 */
export function injectSupersedeFindingsMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: FindingSupersedeVars): Promise<FindingView[]> => {
      const { data, error } = await supersedeFindingsApiFindingsSupersedePost({
        body: { finding_ids: [...vars.findingIds], note: vars.note, superseded_by: vars.supersededBy },
        throwOnError: false,
      });
      if (error) throw error;
      return data!;
    },
    onSuccess: () => invalidateFindingCaches(queryClient),
  }));
}

/**
 * Reopens every named finding, undoing whichever exit or `gone` fact was newest —
 * `POST /api/findings/reopen`. 404 for an unknown id, 422 for a blank note.
 * {@link invalidateFindingCaches}'s own direct-invalidation shape.
 */
export function injectReopenFindingsMutation() {
  const queryClient = inject(QueryClient);
  return injectMutation(() => ({
    mutationFn: async (vars: FindingExitVars): Promise<FindingView[]> => {
      const { data, error } = await reopenFindingsApiFindingsReopenPost({
        body: { finding_ids: [...vars.findingIds], note: vars.note },
        throwOnError: false,
      });
      if (error) throw error;
      return data!;
    },
    onSuccess: () => invalidateFindingCaches(queryClient),
  }));
}
