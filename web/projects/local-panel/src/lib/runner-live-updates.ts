import { DestroyRef, EnvironmentInjector, Injectable, type Signal, effect, inject, untracked } from '@angular/core';
import { QueryClient } from '@tanstack/angular-query-experimental';
import {
  RUNNER_EVENT_STREAM_URL,
  RUNNER_EVENT_TYPES,
  type RunnerEventPayload,
  type RunnerEventType,
  type SseHandle,
  type SseStatus,
  SseService,
} from 'fleet';

import { runnerChunkDetailKey, runnerDashboardKey, runnerLeasesKey } from './query-keys';
import { SessionRecovery } from './session-recovery';

/**
 * A runner event that names a chunk stales that chunk's own detail key — the pass-
 * through pause fact (`runnerApi.ChunkHeaderView`) is hub-sourced, so no
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
 * counterpart of `fleet`'s {@link "./fleet-live".FleetLiveUpdates} (D10): one SSE
 * subscription to the runner's own `GET /api/events/stream`, through
 * {@link SseService} imported unchanged from `fleet`, that invalidates
 * `local-panel`'s TanStack queries through {@link RUNNER_EVENT_INVALIDATION_REGISTRY}
 * — a lookup, never a hand-written `case`.
 *
 * Gap recovery mirrors the hub's `FleetLiveUpdates`: a reconnect (`handle.reopens()`)
 * invalidates the whole query client, so any event missed while the socket was down
 * is closed by a fresh read on top of the transport's own `last_event_id` replay.
 *
 * D9: a stream `401` is terminal — `SseService` schedules no reconnect past one — and
 * is the one place this service reaches outside its own query-invalidation concern: it
 * calls {@link "./session-recovery".SessionRecovery.recoverFromUnauthenticated}, the
 * same classify-and-bounce seam the response interceptor drives, so a session that
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
  private handle: SseHandle<RunnerEventPayload> | null = null;

  /** Connection lifecycle, or `idle` before {@link start}. */
  get status(): Signal<SseStatus> {
    return this.handle?.status ?? IDLE_STATUS;
  }

  /**
   * Open the live stream and wire it to the query cache. Idempotent — a second call
   * is a no-op. Auto-closes on the caller's {@link DestroyRef} (the app teardown).
   */
  start(): void {
    if (this.handle) return;
    const handle = this.sse.connect<RunnerEventPayload>(RUNNER_EVENT_STREAM_URL, {
      events: [...RUNNER_EVENT_TYPES],
    });
    this.handle = handle;

    const sub = handle.events.subscribe(({ type, data }) => this.dispatch(type, data));

    // Reconnect-then-re-GET: a fresh reconnect re-reads the whole tree to close any
    // gap, mirroring fleet-live.ts's own watcher. `untracked` around the `effect()`
    // call itself — `start()` may be invoked from within another reactive context —
    // for the same NG0602 reason `fleet-live.ts` documents at its own call site.
    let lastReopens = handle.reopens();
    const reopenRef = untracked(() =>
      effect(
        () => {
          const reopens = handle.reopens();
          if (reopens > lastReopens) {
            lastReopens = reopens;
            void this.queryClient.invalidateQueries();
          }
        },
        { injector: this.injector },
      ),
    );

    // D9: a stream 401 is terminal — SseService schedules no reconnect past one —
    // so this is the only place that ever observes it; route it into the shared
    // recovery seam rather than leave the closed connection with nothing acting on it.
    const authRef = untracked(() =>
      effect(
        () => {
          if (handle.authFailed()) void this.sessionRecovery.recoverFromUnauthenticated();
        },
        { injector: this.injector },
      ),
    );

    this.destroyRef.onDestroy(() => {
      sub.unsubscribe();
      reopenRef.destroy();
      authRef.destroy();
      handle.close();
      this.handle = null;
    });
  }

  /** Look up this frame's type in {@link RUNNER_EVENT_INVALIDATION_REGISTRY} and
   * invalidate every key it names — a lookup, not a per-event `case`; see that
   * registry's own doc for what each event type stales. */
  private dispatch(type: string, data: RunnerEventPayload): void {
    const keys = RUNNER_EVENT_INVALIDATION_REGISTRY[type as RunnerEventType]?.(data) ?? [];
    for (const queryKey of keys) {
      void this.queryClient.invalidateQueries({ queryKey });
    }
  }
}

/** A frozen `idle` status used before the stream is opened. */
const IDLE_STATUS: Signal<SseStatus> = (() => {
  const s = () => 'idle' as const;
  return s as Signal<SseStatus>;
})();
