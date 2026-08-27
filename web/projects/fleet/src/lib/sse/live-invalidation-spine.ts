import { type DestroyRef, type EffectRef, type EnvironmentInjector, type Signal, effect, untracked } from '@angular/core';
import { QueryClient } from '@tanstack/angular-query-experimental';
import type { Subscription } from 'rxjs';

import { type SseHandle, type SseStatus, SseService } from './sse.service';

/** {@link LiveInvalidationSpine}'s construction parameters — everything a daemon's own
 * live-update service already injects, plus what makes the spine *its* instance: the
 * stream to open, the event union it carries, and the registry mapping each kind to
 * the query keys it stales. `onFrame`/`onAuthFailed` are the two hooks a daemon's own
 * service still owns directly (`review:F5`) — an event-log tee for the hub, the D9
 * session-recovery bounce for the runner — neither of which the spine knows about. */
export interface LiveInvalidationSpineOptions<TPayload extends object, TType extends string> {
  sse: SseService;
  queryClient: QueryClient;
  injector: EnvironmentInjector;
  destroyRef: DestroyRef;
  streamUrl: string;
  eventTypes: readonly TType[];
  registry: Record<TType, (data: TPayload) => readonly (readonly unknown[])[]>;
  coalesceWindowMs: number;
  onFrame?: (type: TType, data: TPayload) => void;
  onAuthFailed?: () => void;
}

/**
 * The registry-driven coalescing/reconnect machinery both daemons' live-update
 * services drive (`review:F5`; D10, `bzh:frontend-disjoint-diffs` — the machinery
 * lifted here is kind-agnostic, unlike each daemon's own registry, which stays put).
 * One SSE subscription, dispatched through the caller's registry into a coalesced
 * `invalidateQueries` pass, plus reconnect-then-re-GET gap recovery: identical to what
 * `fleet-live.ts` and `runner-live-updates.ts` each hand-carried before this extraction.
 */
export class LiveInvalidationSpine<TPayload extends object, TType extends string> {
  private handle: SseHandle<TPayload> | null = null;
  private sub: Subscription | null = null;
  private reopenRef: EffectRef | null = null;
  private authRef: EffectRef | null = null;
  /** Guards {@link DestroyRef.onDestroy} registration to exactly once per instance —
   * {@link open} runs on every {@link start}/{@link restart}, but the teardown it
   * registers must not stack (D2): a second registration would run the same close
   * twice on the eventual real destroy, once each against whatever handle/effects
   * happen to be current then. */
  private destroyRegistered = false;
  /** Keys queued by {@link dispatch} since the last flush, keyed by their serialized
   * form so a repeated key collapses to one entry regardless of how many frames named
   * it. */
  private readonly pendingInvalidations = new Map<string, readonly unknown[]>();
  private flushTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(private readonly opts: LiveInvalidationSpineOptions<TPayload, TType>) {}

  /** Connection lifecycle for a header status dot, or `idle` before {@link start}. */
  get status(): Signal<SseStatus> {
    return this.handle?.status ?? IDLE_STATUS;
  }

  /** `true` once the stream closed on a `401` — a session that expired mid-stream.
   * `false` before {@link start} and for the whole life of a stream that never sees one. */
  get authFailed(): Signal<boolean> {
    return this.handle?.authFailed ?? FALSE_STATUS;
  }

  /**
   * Open the live stream and wire it to the query cache. Idempotent — a second call
   * is a no-op. Auto-closes on the caller's {@link DestroyRef} (the app teardown).
   */
  start(): void {
    if (this.handle) return;
    this.open();
  }

  /**
   * Tear down the current stream and open a fresh one — for a caller that declined
   * session recovery and wants another shot at the stream (blizzard#333 D2/D3), never
   * for `SseService`'s own reconnect, which already retries under the hood. Leaves
   * exactly one subscription, effect pair, and destroy teardown live afterward, same
   * as a single {@link start} would — {@link open} registers the teardown once, ever,
   * regardless of how many times {@link start}/{@link restart} run it.
   */
  restart(): void {
    this.teardown();
    this.open();
  }

  private open(): void {
    const { sse, queryClient, injector, destroyRef, streamUrl, eventTypes, onFrame, onAuthFailed } = this.opts;
    const handle = sse.connect<TPayload>(streamUrl, { events: [...eventTypes] });
    this.handle = handle;

    this.sub = handle.events.subscribe(({ type, data }) => {
      onFrame?.(type as TType, data);
      this.dispatch(type as TType, data);
    });

    // Reconnect-then-re-GET: a fresh reconnect re-reads the whole tree to close any
    // gap. `untracked` around the `effect()` call itself (not its body): `open()` may
    // be invoked from within another reactive context, and `effect()` asserts it is
    // not called from one (NG0602) — `untracked` clears the active consumer for the
    // duration of this call, so `start()`/`restart()` stay safe regardless of their
    // caller's context.
    let lastReopens = handle.reopens();
    this.reopenRef = untracked(() =>
      effect(
        () => {
          const reopens = handle.reopens();
          if (reopens > lastReopens) {
            lastReopens = reopens;
            void queryClient.invalidateQueries();
          }
        },
        { injector },
      ),
    );

    // D9: a stream 401 is terminal — `SseService` schedules no reconnect past one —
    // so this is the only place that ever observes it. Only a caller that names
    // `onAuthFailed` wants this watched at all (the hub instead exposes `authFailed`
    // for its app root to route on).
    this.authRef = onAuthFailed
      ? untracked(() =>
          effect(
            () => {
              if (handle.authFailed()) onAuthFailed();
            },
            { injector },
          ),
        )
      : null;

    if (!this.destroyRegistered) {
      this.destroyRegistered = true;
      destroyRef.onDestroy(() => this.teardown());
    }
  }

  /** Close the current stream and release its subscription/effects/pending flush —
   * shared by the real destroy teardown and {@link restart}'s manual one, so neither
   * can drift out of sync with what {@link open} actually wires up. */
  private teardown(): void {
    this.sub?.unsubscribe();
    this.reopenRef?.destroy();
    this.authRef?.destroy();
    this.handle?.close();
    if (this.flushTimer !== null) {
      clearTimeout(this.flushTimer);
      this.flushTimer = null;
    }
    this.handle = null;
    this.sub = null;
    this.reopenRef = null;
    this.authRef = null;
  }

  /** Queue this frame's keys for the next flush rather than invalidating them
   * immediately — a burst of frames whose key sets overlap collapses to one
   * invalidation pass per distinct key within {@link LiveInvalidationSpineOptions.coalesceWindowMs}.
   * Reconnect-driven whole-tree invalidation (the `reopens()` watcher in {@link start})
   * bypasses this entirely and stays synchronous. */
  private dispatch(type: TType, data: TPayload): void {
    const keys = this.opts.registry[type]?.(data) ?? [];
    for (const queryKey of keys) {
      this.pendingInvalidations.set(JSON.stringify(queryKey), queryKey);
    }
    if (keys.length > 0 && this.flushTimer === null) {
      this.flushTimer = setTimeout(() => this.flushInvalidations(), this.opts.coalesceWindowMs);
    }
  }

  /** Invalidate every key accumulated since the last flush, once each. */
  private flushInvalidations(): void {
    this.flushTimer = null;
    const keys = [...this.pendingInvalidations.values()];
    this.pendingInvalidations.clear();
    for (const queryKey of keys) {
      void this.opts.queryClient.invalidateQueries({ queryKey });
    }
  }
}

/** A frozen `idle` status used before the stream is opened. */
const IDLE_STATUS: Signal<SseStatus> = (() => {
  const s = () => 'idle' as const;
  return s as Signal<SseStatus>;
})();

/** A frozen `false` used for {@link LiveInvalidationSpine.authFailed} before the stream is opened. */
const FALSE_STATUS: Signal<boolean> = (() => {
  const s = () => false;
  return s as Signal<boolean>;
})();
