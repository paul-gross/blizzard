import { DestroyRef, EnvironmentInjector, Injectable, inject } from '@angular/core';
import { QueryClient } from '@tanstack/angular-query-experimental';
import {
  INVALIDATION_COALESCE_WINDOW_MS,
  LiveInvalidationSpine,
  RUNNER_EVENT_STREAM_URL,
  RUNNER_EVENT_TYPES,
  type RunnerEventPayload,
  type RunnerEventType,
  SseService,
} from 'fleet';

import { runnerChunkDetailKey, runnerDashboardKey, runnerLeasesKey } from './query-keys';
import { SessionRecovery } from './session-recovery';

/**
 * A runner event that names a chunk stales that chunk's own detail key — the pass-
 * through pause fact (`runnerApi.ChunkDetail`) is hub-sourced, so no
 * runner event proves it directly, but a frame naming the chunk is the closest local
 * signal that something about it moved, and the key's own backstop (D7) closes the
 * rest. Every {@link RunnerEventPayload} shape that carries a `chunk_id` shares this.
 */
function chunkDetailKeys(data: RunnerEventPayload): readonly (readonly unknown[])[] {
  return data.chunk_id ? [runnerChunkDetailKey(data.chunk_id)] : [];
}

/**
 * The event → query-key invalidation registry (blizzard#317 Phase 4) — `local-panel`'s
 * own instance of the pattern `fleet`'s `EVENT_INVALIDATION_REGISTRY` established
 * (`sse/fleet-live.ts`), not an extension of it (D10, `bzh:frontend-disjoint-diffs`):
 * the event union type comes from `fleet`'s runner vocabulary
 * ({@link RunnerEventType}), but the query keys each kind maps to are
 * `local-panel`'s own, so a second, cross-daemon registry lives here rather than
 * growing a `case` onto the hub's. `Record<RunnerEventType, …>`, exhaustive over
 * {@link RUNNER_EVENT_TYPES} — a new runner event type is a compile error here until
 * it is given a row, the same guard `fleet-live.ts` carries for the hub's own union.
 *
 * `GET /api/dashboard` ({@link runnerDashboardKey}) folds six of the panel's seven
 * local sections (`runner`, `environments`, `asks`, `escalations`, `takeovers`,
 * `facts`) into one read (issue #311), so every runner event kind stales it — each
 * kind reports a change to exactly one of those sections. `lease-changed`
 * additionally moves the `runner.capacities.used` count that same `runner` section
 * reports (`RunnerStatusService.summary` counts active leases), and is the only kind
 * that also stales the separate {@link runnerLeasesKey} read, the panel's own `GET
 * /api/leases` liveness rail. See {@link chunkDetailKeys} for the chunk-scoped key
 * every kind shares.
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
 * The panel's live-update spine (blizzard#317 Phase 4) — the runner-scoped
 * counterpart of `fleet`'s {@link "./fleet-live".FleetLiveUpdates} (D10). The
 * coalescing dispatch and reconnect-then-re-GET gap recovery are
 * {@link LiveInvalidationSpine}'s (`review:F5`), configured here with
 * {@link RUNNER_EVENT_INVALIDATION_REGISTRY} — a lookup, never a hand-written `case`.
 *
 * D9: a stream `401` is terminal — `SseService` schedules no reconnect past one — and
 * is the one thing this service still handles itself, via the spine's `onAuthFailed`
 * hook: it calls {@link "./session-recovery".SessionRecovery.recoverFromUnauthenticated},
 * the same classify-and-bounce seam the response interceptor drives, so a session that
 * expires mid-stream routes through the one recovery path both callers share rather
 * than leaving a closed connection nothing acts on.
 */
@Injectable({ providedIn: 'root' })
export class RunnerLiveUpdates {
  private readonly sse = inject(SseService);
  private readonly queryClient = inject(QueryClient);
  private readonly injector = inject(EnvironmentInjector);
  private readonly destroyRef = inject(DestroyRef);
  private readonly sessionRecovery = inject(SessionRecovery);
  private readonly spine = new LiveInvalidationSpine<RunnerEventPayload, RunnerEventType>({
    sse: this.sse,
    queryClient: this.queryClient,
    injector: this.injector,
    destroyRef: this.destroyRef,
    streamUrl: RUNNER_EVENT_STREAM_URL,
    eventTypes: RUNNER_EVENT_TYPES,
    registry: RUNNER_EVENT_INVALIDATION_REGISTRY,
    coalesceWindowMs: INVALIDATION_COALESCE_WINDOW_MS,
    onAuthFailed: () => void this.sessionRecovery.recoverFromUnauthenticated(),
  });

  /**
   * Open the live stream and wire it to the query cache. Idempotent — a second call
   * is a no-op. Auto-closes on the caller's {@link DestroyRef} (the app teardown).
   */
  start(): void {
    this.spine.start();
  }
}
