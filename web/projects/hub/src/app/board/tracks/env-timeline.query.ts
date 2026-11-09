import { inject } from '@angular/core';
import { QueryClient, injectQuery, keepPreviousData } from '@tanstack/angular-query-experimental';

import { FLEET_CLOCK, envTimelineApiEnvTimelineGet, hubEnvTimelineKey, hubEnvTimelineLiveKey } from 'fleet';
import { LIVE_COVERED_POLL_BACKSTOP_MS, injectProjectFilter, lensedKey } from 'fleet/shell';

import {
  type EnvTimelineView,
  FULL_READ_EVERY_MS,
  type LiveTimeline,
  catchUp,
  expandTimeline,
  mergeLatest,
  mergeWindow,
} from './env-timeline.model';
import { LOOKBACK_MS, type TimelineRange } from './tracks-window.model';

/** How far past its span a live window reads back, so a short pan or the minutes since still land in its data. */
const LIVE_SLACK_MS = 60 * 60_000;

async function readTimeline(query: { since: string; until?: string; latest?: boolean; project?: string }): Promise<EnvTimelineView> {
  const { data, error } = await envTimelineApiEnvTimelineGet({ query, throwOnError: false });
  if (error) throw error;
  return expandTimeline(data);
}

function isoBefore(nowMs: number, ms: number): string {
  return new Date(nowMs - ms).toISOString();
}

/**
 * The window following now: every node step of the last `coverMs` (and an hour's slack), kept current by catch-up
 * reads. The key holds the covered span, not the window's bounds, so the minute ticking on never refetches it, and
 * any window inside the covered span — a shorter chip, a short pan back — reads from it with no request.
 */
export function injectHubEnvTimelineLiveQuery(coverMs: () => number) {
  const projectFilter = injectProjectFilter();
  const client = inject(QueryClient);
  const clock = inject(FLEET_CLOCK);
  return injectQuery(() => {
    const queryKey = lensedKey([...hubEnvTimelineLiveKey, 'window', coverMs()], projectFilter());
    const project = projectFilter() ?? undefined;
    return {
      queryKey,
      queryFn: () => {
        const now = clock();
        return catchUp(
          client.getQueryData<LiveTimeline>(queryKey),
          now,
          () => readTimeline({ since: isoBefore(now, coverMs() + LIVE_SLACK_MS), project }),
          (since) => readTimeline({ since, project }),
          mergeWindow,
        );
      },
      placeholderData: keepPreviousData,
      refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
    };
  });
}

/**
 * What every environment is doing now — each one's newest step over the hub's widest read, whatever window the tracks
 * show — kept current by catch-up reads of the environments that moved.
 */
export function injectHubEnvTimelineNowQuery() {
  const projectFilter = injectProjectFilter();
  const client = inject(QueryClient);
  const clock = inject(FLEET_CLOCK);
  return injectQuery(() => {
    const queryKey = lensedKey([...hubEnvTimelineLiveKey, 'now'], projectFilter());
    const project = projectFilter() ?? undefined;
    return {
      queryKey,
      queryFn: () => {
        const now = clock();
        return catchUp(
          client.getQueryData<LiveTimeline>(queryKey),
          now,
          () => readTimeline({ since: isoBefore(now, LOOKBACK_MS), latest: true, project }),
          (since) => readTimeline({ since, latest: true, project }),
          mergeLatest,
        );
      },
      refetchInterval: LIVE_COVERED_POLL_BACKSTOP_MS,
    };
  });
}

/**
 * A pinned window outside what the live read covers, read at `range`'s bounds; idle while `range` is `null`. A range
 * wholly in the past never goes stale.
 */
export function injectHubEnvTimelineRangeQuery(range: () => TimelineRange | null) {
  const projectFilter = injectProjectFilter();
  const clock = inject(FLEET_CLOCK);
  return injectQuery(() => {
    const r = range();
    const settled = r !== null && Date.parse(r.until) < clock() - FULL_READ_EVERY_MS;
    return {
      queryKey: lensedKey([...hubEnvTimelineKey, 'range', r?.since ?? null, r?.until ?? null], projectFilter()),
      enabled: r !== null,
      queryFn: () => readTimeline({ since: r?.since ?? '', until: r?.until, project: projectFilter() ?? undefined }),
      placeholderData: keepPreviousData,
      staleTime: settled ? Infinity : 0,
      refetchInterval: settled ? false : LIVE_COVERED_POLL_BACKSTOP_MS,
    };
  });
}
