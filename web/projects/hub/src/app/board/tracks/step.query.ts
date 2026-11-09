import { injectQuery } from '@tanstack/angular-query-experimental';

import { envTimelineStepApiEnvTimelineStepGet, hubEnvTimelineLiveKey, type hubApi } from 'fleet';
import { LIVE_COVERED_POLL_BACKSTOP_MS } from 'fleet/shell';

import type { StepRef } from './step.model';

export type EnvTimelineStepView = hubApi.EnvTimelineStepView;

/**
 * A step read's answer: the step and its chunk's path, or `null` for a chunk with no node step yet — one still queued,
 * blocked, or never picked up, which the hub answers `404`.
 */
export type StepReadView = EnvTimelineStepView | null;

/** How long a step's detail reads fresh between the live events that re-read it. */
const STEP_FRESH_MS = 30_000;

/**
 * The `GET /api/env-timeline/step` read for one node step — shared by the drawer and a hover's prefetch. Keyed under the
 * live root, so every event that moves a block on the tracks re-reads the step on screen too.
 */
export function stepQueryOptions(step: StepRef | null) {
  const session = step?.session ?? null;
  const at = step?.at ?? null;
  return {
    queryKey: [...hubEnvTimelineLiveKey, 'step', step?.chunkId ?? null, session, at] as const,
    queryFn: async (): Promise<StepReadView> => {
      const { data, error, response } = await envTimelineStepApiEnvTimelineStepGet({
        query: {
          chunk_id: step?.chunkId ?? '',
          ...(session !== null ? { lease_id: session } : at !== null ? { started_at: at } : {}),
        },
        throwOnError: false,
      });
      if (response?.status === 404) return null;
      if (error) throw error;
      return data;
    },
    staleTime: STEP_FRESH_MS,
  };
}

/** Whether a step read's answer is a step still running — its end, its spend and its chunk's path still move. */
export function stepRunning(view: StepReadView | undefined): boolean {
  return view?.step.segment.ended_at === null;
}

/** One node step in depth; idle while no step is selected. A running step re-reads on the poll backstop as well. */
export function injectHubEnvTimelineStepQuery(key: () => StepRef | null) {
  return injectQuery(() => {
    const step = key();
    return {
      ...stepQueryOptions(step),
      enabled: step !== null,
      refetchInterval: (query: { state: { data: StepReadView | undefined } }) =>
        stepRunning(query.state.data) ? LIVE_COVERED_POLL_BACKSTOP_MS : false,
      // Another step of the same chunk keeps the chunk's path, and its reference column, on screen while it reads.
      placeholderData: (prev: StepReadView | undefined) =>
        prev != null && prev.step.segment.chunk_id === step?.chunkId ? prev : undefined,
    };
  });
}
