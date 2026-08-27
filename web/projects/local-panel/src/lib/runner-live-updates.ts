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

import { runnerChunkDetailKey, runnerDashboardKey, runnerLeasesKey } from './query-keys';
import { SessionRecovery } from './session-recovery';

/** Cap on the stream's own re-arm attempts (blizzard#333 D3) — a no-session `401` the
 * seam can classify definitively is never retried here (`SessionRecovery` already owns
 * that outcome); this bounds only the "session read itself failed" shape, the same
 * defect `SseService`'s terminal-401 contract was built to avoid: a session that will
 * never resolve must not be retried forever. Small and fixed, not configurable — there
 * is no dial an operator would ever want to turn on it. */
export const STREAM_REARM_MAX_ATTEMPTS = 3;

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
 *
 * blizzard#333 D2/D3: a `401` the seam cannot classify — the session read itself
 * failed, the daemon-restart shape — is worth another shot at the stream, since it
 * says nothing about whether the session is actually gone. That one outcome re-arms
 * the stream via {@link LiveInvalidationSpine.restart}, on `SseService`'s own
 * `backoffDelay` ladder, capped at {@link STREAM_REARM_MAX_ATTEMPTS} attempts — bounded
 * for the same reason `SseService`'s terminal `401` is: a session that never resolves
 * must not be retried forever. Every other classification is already answered by the
 * seam itself (a bounce, or {@link "./session-recovery".SessionRecovery.recovering}),
 * so none of them re-arms.
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

  /** Connection lifecycle for the header status, mirroring `fleet`'s
   * `FleetLiveUpdates.status` — `idle` before {@link start}. */
  get status(): Signal<SseStatus> {
    return this.spine.status;
  }

  /** `true` once the stream closed on a `401` — a session that expired mid-stream,
   * mirroring `fleet`'s `FleetLiveUpdates.authFailed`. `false` before {@link start}
   * and for the whole life of a stream that never sees one; also `false` again once a
   * bounded re-arm (D2/D3) opens a fresh attempt. */
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

  /** Classify the stream's `401` and, for the one outcome that is not a definitive
   * answer — the session read itself failed — re-arm the stream on a bounded backoff
   * (D2/D3). Every other outcome is already handled by `SessionRecovery` itself. */
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
