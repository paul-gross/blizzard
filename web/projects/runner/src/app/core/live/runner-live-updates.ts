import { DestroyRef, EnvironmentInjector, Injectable, type Signal, inject } from '@angular/core';
import { QueryClient } from '@tanstack/angular-query-experimental';
import {
  backoffDelay,
  INVALIDATION_COALESCE_WINDOW_MS,
  LiveInvalidationSpine,
  RUNNER_EVENT_STREAM_URL,
  RUNNER_EVENT_TYPES,
  type RunnerEventPayload,
  type RunnerEventType,
  SseService,
  type SseStatus,
} from 'fleet';

import { runnerChunkDetailKey, runnerDashboardKey, runnerLeasesKey } from '../query-keys';
import { SessionRecovery } from '../identity/session-recovery';

/** Cap on the stream's own re-arm attempts after a `401` whose session read itself
 * failed: a session that never resolves must not be retried forever. */
export const STREAM_REARM_MAX_ATTEMPTS = 3;

/**
 * A runner event that names a chunk stales that chunk's own detail key: no runner
 * event proves the hub-sourced pause fact directly, but a frame naming the chunk is
 * the closest local signal that something about it moved.
 */
function chunkDetailKeys(data: RunnerEventPayload): readonly (readonly unknown[])[] {
  return data.chunk_id ? [runnerChunkDetailKey(data.chunk_id)] : [];
}

/**
 * The event → query-key invalidation registry (`bzh:frontend-disjoint-diffs`),
 * exhaustive over {@link RUNNER_EVENT_TYPES}: a new event type is a compile error
 * here until it is given a row.
 *
 * Every kind stales {@link runnerDashboardKey}, since the dashboard read folds each
 * kind's section. `lease-changed` alone also stales {@link runnerLeasesKey}.
 */
const RUNNER_EVENT_INVALIDATION_REGISTRY: Record<
  RunnerEventType,
  (data: RunnerEventPayload) => readonly (readonly unknown[])[]
> = {
  'lease-changed': (data) => [runnerLeasesKey, runnerDashboardKey, ...chunkDetailKeys(data)],
  'ask-changed': (data) => [runnerDashboardKey, ...chunkDetailKeys(data)],
  'escalation-changed': (data) => [runnerDashboardKey, ...chunkDetailKeys(data)],
  'takeover-changed': (data) => [runnerDashboardKey, ...chunkDetailKeys(data)],
  'environment-changed': (data) => [runnerDashboardKey, ...chunkDetailKeys(data)],
  'fact-changed': (data) => [runnerDashboardKey, ...chunkDetailKeys(data)],
};

/**
 * The runner's live-update service: {@link LiveInvalidationSpine} configured with
 * {@link RUNNER_EVENT_INVALIDATION_REGISTRY}.
 *
 * A stream `401` goes to {@link SessionRecovery.recoverFromUnauthenticated}. Only
 * the `read-failed` outcome re-arms the stream, on a backoff capped at
 * {@link STREAM_REARM_MAX_ATTEMPTS}; every other outcome is already answered.
 */
@Injectable({ providedIn: 'root' })
export class RunnerLiveUpdates {
  private readonly sse = inject(SseService);
  private readonly queryClient = inject(QueryClient);
  private readonly injector = inject(EnvironmentInjector);
  private readonly destroyRef = inject(DestroyRef);
  private readonly sessionRecovery = inject(SessionRecovery);
  private rearmAttempt = 0;
  private rearmTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly spine = new LiveInvalidationSpine<RunnerEventPayload, RunnerEventType>({
    sse: this.sse,
    queryClient: this.queryClient,
    injector: this.injector,
    destroyRef: this.destroyRef,
    streamUrl: RUNNER_EVENT_STREAM_URL,
    eventTypes: RUNNER_EVENT_TYPES,
    registry: RUNNER_EVENT_INVALIDATION_REGISTRY,
    coalesceWindowMs: INVALIDATION_COALESCE_WINDOW_MS,
    onAuthFailed: () => void this.handleAuthFailed(),
  });

  constructor() {
    this.destroyRef.onDestroy(() => {
      if (this.rearmTimer !== null) clearTimeout(this.rearmTimer);
    });
  }

  /** Connection lifecycle for a header status dot — `idle` before {@link start}. */
  get status(): Signal<SseStatus> {
    return this.spine.status;
  }

  /** `true` once the stream closed on a `401` — a session that expired mid-stream.
   * `false` before {@link start}, and again once a bounded re-arm opens a fresh attempt. */
  get authFailed(): Signal<boolean> {
    return this.spine.authFailed;
  }

  /**
   * Open the live stream and wire it to the query cache. Idempotent — a second call
   * is a no-op. Auto-closes on the caller's {@link DestroyRef} (the app teardown).
   */
  start(): void {
    this.spine.start();
  }

  /** Classify the stream's `401` and re-arm the stream on a bounded backoff when the
   * outcome is `read-failed`. */
  private async handleAuthFailed(): Promise<void> {
    const outcome = await this.sessionRecovery.recoverFromUnauthenticated();
    if (outcome !== 'read-failed' || this.rearmAttempt >= STREAM_REARM_MAX_ATTEMPTS) return;

    this.rearmAttempt += 1;
    this.rearmTimer = setTimeout(() => {
      this.rearmTimer = null;
      this.spine.restart();
    }, backoffDelay(this.rearmAttempt));
  }
}
