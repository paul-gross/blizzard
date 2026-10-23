import { DestroyRef, EnvironmentInjector, Injectable, type Signal, inject } from '@angular/core';
import { QueryClient } from '@tanstack/angular-query-experimental';
import { backoffDelay, INVALIDATION_COALESCE_WINDOW_MS, LiveInvalidationSpine, openLookup, RUNNER_EVENT_STREAM_URL, RUNNER_EVENT_TYPES, type RunnerEventPayload, type RunnerEventPayloads, type RunnerEventType, SseService, type SseStatus } from 'fleet/runner-shell';

import { hostKey, runnerChunkDetailKey, runnerDashboardKey, runnerLeasesKey } from '../query-keys';
import { SessionRecovery } from '../identity/session-recovery';

/** Cap on the stream's own re-arm attempts after a `401` whose session read itself
 * failed: a session that never resolves must not be retried forever. */
export const STREAM_REARM_MAX_ATTEMPTS = 3;

/** A runner-scoped frame's own keys — each under the runner id the frame names, so a
 * frame from one runner never stales another's. Every kind stales that runner's
 * dashboard, since the dashboard read folds each kind's section, and a frame naming a
 * chunk stales that chunk's detail on that runner: no runner event proves the
 * hub-sourced pause fact directly, but a frame naming the chunk is the closest local
 * signal that something about it moved. */
function runnerKeys(data: { readonly runner_id: string; readonly chunk_id?: string | null }): readonly (readonly unknown[])[] {
  const keys: (readonly unknown[])[] = [runnerDashboardKey(data.runner_id)];
  if (data.chunk_id) keys.push(runnerChunkDetailKey(data.runner_id, data.chunk_id));
  return keys;
}

/**
 * The event → query-key invalidation registry (`bzh:frontend-disjoint-diffs`),
 * exhaustive over {@link RUNNER_EVENT_TYPES}: a new event type is a compile error
 * here until it is given a row, and a type a newer host sends before then stales nothing.
 *
 * The host serves one stream for every runner it holds. A runner-scoped frame carries
 * its `runner_id` and stales only that runner's keys ({@link runnerKeys});
 * `lease-changed` alone also stales that runner's {@link runnerLeasesKey}. Every frame,
 * `environment-changed` (host-scoped, naming no runner) included, stales {@link hostKey},
 * which keeps the host page's environments, subscriptions, and runner rows as fresh as
 * every frame.
 */
const RUNNER_EVENT_INVALIDATION_REGISTRY: { [K in RunnerEventType]: (data: RunnerEventPayloads[K]) => readonly (readonly unknown[])[] } = {
  'lease-changed': (data) => [runnerLeasesKey(data.runner_id), ...runnerKeys(data), hostKey],
  'ask-changed': (data) => [...runnerKeys(data), hostKey],
  'escalation-changed': (data) => [...runnerKeys(data), hostKey],
  'takeover-changed': (data) => [...runnerKeys(data), hostKey],
  'environment-changed': () => [hostKey],
  'fact-changed': (data) => [...runnerKeys(data), hostKey],
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
    registry: openLookup(
      RUNNER_EVENT_INVALIDATION_REGISTRY as Record<RunnerEventType, (data: RunnerEventPayload) => readonly (readonly unknown[])[]>,
      () => () => [],
    ),
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
