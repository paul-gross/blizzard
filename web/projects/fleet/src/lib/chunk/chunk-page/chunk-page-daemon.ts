import { InjectionToken, type Provider, type Signal } from '@angular/core';

import type { AnswerQuestionEvent, ResolveDecisionEvent } from '../chunk-detail/chunk-awaiting-human';
import type { DaemonTarget } from '../../core/daemon-target/daemon-target';

/**
 * The operator-action port a daemon may hand {@link ChunkPage} — everything about the
 * page that depends on *who may act* and *how an action writes*, kept off the shared
 * page so it injects no daemon's auth or mutations itself. A daemon that provides none
 * renders the page read-only.
 *
 * Built per page instance ({@link ChunkPageDaemon.actions} is a factory the page calls
 * from its own injection context), so the notice channels and mutation state belong to
 * one visit of one chunk and never outlive it.
 */
export interface ChunkPageActions {
  /** Whether the identity may answer an open question (`question:answer`). */
  readonly canAnswer: Signal<boolean>;
  /** Whether the identity may resolve an open gate decision (`gate:resolve`). */
  readonly canResolve: Signal<boolean>;
  /** Whether the identity may read the chunk's transcripts (`transcript:read`). */
  readonly canReadTranscripts: Signal<boolean>;
  /** Whether a resolve is in flight, so a double tap cannot resolve the gate twice. */
  readonly resolvePending: Signal<boolean>;
  /** The questions an answer is in flight for, so a double tap cannot answer twice. */
  readonly pendingAnswerQuestionIds: Signal<readonly string[]>;
  /** The last action's failure, or `null` — cleared on the next attempt. */
  readonly actionError: Signal<string | null>;
  /** The last action's non-failure outcome that still needs saying (a lost answer race
   * naming the winner), or `null` — cleared alongside {@link actionError}. */
  readonly actionOutcome: Signal<string | null>;
  /** The graphs view's own path segments a graph badge links to. */
  readonly graphLinkBase: readonly string[];
  /** The events view's own path segments the chunk page links its own events to, as
   * `<base>?chunk=<id>` — a daemon serving no events feed provides no port, so no link. */
  readonly eventsLinkBase: readonly string[];
  answer(event: AnswerQuestionEvent): void;
  resolve(event: ResolveDecisionEvent): void;
}

/**
 * The daemon {@link ChunkPage} reads its chunk from. `target` is the generated
 * transport, the `runner_id` its routes are addressed under, and the key root whose
 * live-update registry keeps the reads current. Both members are accessors because a
 * route's providers outlive one visit, so the daemon may change without remounting.
 */
export interface ChunkPageDaemon {
  /** The daemon every read on the page crosses to, or `null` — every read held. */
  readonly target: () => DaemonTarget | null;
  /** The board view's path segments the page links back to; its chunk links sit under
   * `<base>/chunk`. */
  readonly boardLinkBase: () => readonly string[];
  /** The operator-action port's factory, called from the page's injection context;
   * absent renders the page read-only. */
  readonly actions?: () => ChunkPageActions;
}

export const CHUNK_PAGE_DAEMON = new InjectionToken<ChunkPageDaemon>('CHUNK_PAGE_DAEMON');

/** The provider a `board/chunk/:chunkId` route carries to mount {@link ChunkPage} — the
 * factory runs in the route's injection context, so a daemon may inject what it reads its
 * target from. */
export function provideChunkPageDaemon(daemon: () => ChunkPageDaemon): Provider {
  return { provide: CHUNK_PAGE_DAEMON, useFactory: daemon };
}
