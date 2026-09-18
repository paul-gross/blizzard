import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import type { ChunkDetail, ChunkStatus, PauseView, WorkRefView, RouteView } from '../api/hub';
import { compactRef } from '../compact-ref';
import { KitButton } from '../kit/kit-button';
import { KitConfirmDialog } from '../kit/kit-confirm-dialog';

/** Statuses the hub's `PauseService` refuses to pause (`ChunkNotPausable`), mirrored
 * here so the dock never offers a Pause the server would answer with a 409 (issue #46).
 * A terminal or mid-delivery chunk has no work to stop.
 *
 * `paused` is deliberately **absent**: whether a chunk is already paused is not a
 * question `status` can answer (PAUSED derives below the human-gated states), so it is
 * never asked here — see {@link ChunkDetailHeader.pause}, which owns that half by
 * reading the fact. */
const NOT_PAUSABLE = new Set<ChunkStatus>(['done', 'stopped', 'delivering']);

/** Statuses the hub's `CompleteService` treats as a no-op rather than a transition
 * (issue #294): a `done` chunk is already done, so the dock withholds the control
 * rather than offer a click that writes nothing. Every other status is completable,
 * including `stopped` — unlike Pause/Detach, Complete does not hang off a live route,
 * and unlike Stop there is no un-complete verb, so this set has exactly one member. */
const NOT_COMPLETABLE = new Set<ChunkStatus>(['done']);

/** Statuses with no acquiring runner — the only ones Delete reaches (D8, issue #364):
 * a `not_ready`/`ready` chunk has no live route to release, unlike every status
 * Detach guards. Owned right beside the control it gates, the same shape as
 * {@link NOT_PAUSABLE}/{@link NOT_COMPLETABLE} above. */
const UNACQUIRED_STATUSES = new Set<ChunkStatus>(['not_ready', 'ready']);

/**
 * The chunk detail dock's header (issue #79) — the chunk's identity in the
 * board's own vocabulary (the short name, its work item, its state, and the
 * node it sits at), plus the operator actions that hang off it: the **route +
 * Detach** control (issue #42), **Pause/Resume** (issue #46), **Complete**
 * (issue #294), **Delete** (D8, issue #364), and dismiss.
 *
 * Detach is deliberately **not** requeue — it supersedes no escalation and
 * bumps no epoch, so a `needs_human` chunk detached this way still derives
 * `needs_human` afterward (`src/blizzard/hub/domain/detach.py`); this header
 * never claims otherwise. Pause/Resume switches on the pause **fact**
 * (`ChunkDetail.pause`), never on `status` — a chunk both paused and parked
 * on a question derives `waiting_on_human`, so a status-keyed switch would
 * never offer Resume. **Complete** (issue #294) is the operator's manual
 * counterpart to landing: reachable from any non-`done` status, including
 * `stopped` — unlike Stop, there is no un-complete verb, so the dock offers
 * no way back once clicked. **Delete** (D8, issue #364) withdraws the
 * chunk's hub item(s) outright, reachable only from `not_ready`/`ready`
 * ({@link UNACQUIRED_STATUSES}) — a chunk with an acquiring runner has no
 * live route to release, the same reasoning Detach's own route guard
 * follows. It moved here from the board card, which had no room for a
 * control that invasive.
 *
 * Presentational only: it holds the detail input and emits `dismiss`,
 * `detach`, `pauseChunk`, `resumeChunk`, `complete`, and `delete`; the
 * mutations those events drive live in the container.
 */
@Component({
  selector: 'fleet-chunk-detail-header',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton, KitConfirmDialog, RouterLink],
  templateUrl: './chunk-detail-header.html',
  styleUrl: './chunk-detail-header.css',
})
export class ChunkDetailHeader {
  /** The chunk aggregate to render (identity, status, current node, pause, route). */
  readonly detail = input.required<ChunkDetail>();

  /** Whether the current identity may operate Pause/Resume/Detach (`chunk:control` —
   * issue #210). Withholds every one of those controls when `false` so a `guest`
   * never sees a write it cannot make; `null`/pending resolves to `false` (hidden
   * until confirmed), the same convention `RunnerPanel`'s `canPause` set. */
  readonly canControl = input(false);

  /** The chunk detail route's own path segments, before the chunk id — lets a
   * consumer outside the desktop board point the longname link elsewhere without
   * `fleet` hardcoding a hub route (`ChunkArtifacts`'s own `linkBase` follows the
   * same convention). */
  readonly linkBase = input<readonly string[]>(['/board', 'chunk']);

  /** Emitted when the operator dismisses the dock. */
  readonly dismiss = output<void>();

  /** Emitted with the chunk id when the operator confirms Detach (issue #42). */
  readonly detach = output<string>();

  /** Emitted with the chunk id when the operator confirms Pause (issue #46). */
  readonly pauseChunk = output<string>();

  /** Emitted with the chunk id when the operator confirms Resume (issue #46). */
  readonly resumeChunk = output<string>();

  /** Emitted with the chunk id when the operator confirms Complete (issue #294). */
  readonly complete = output<string>();

  /** Emitted with the chunk id when the operator confirms Delete (D8, issue #364). */
  readonly delete = output<string>();

  protected readonly pendingConfirm = signal<{
    readonly heading: string;
    readonly message: string;
    readonly confirmLabel: string;
    readonly variant: 'primary' | 'danger';
    readonly run: () => void;
  } | null>(null);

  /** The chunk's work refs, for the header — each linked out to its source's web
   * address when the configured binding rendered one (a null `web_url` degrades to
   * plain text, no broken link). */
  protected readonly pointers = computed<readonly WorkRefView[]>(() => this.detail().work_refs ?? []);

  /** The chunk's open operator pause, if any — who set it (issue #46). Read off the
   * detail's `pause` fact, not `status`: a chunk both paused and parked on a question
   * derives `waiting_on_human`, so `status` alone would never surface it.
   *
   * This is also the **Pause/Resume switch**: non-null renders Resume, null renders
   * Pause (subject to {@link pausable}). `status` must never gate Resume. */
  protected readonly pause = computed<PauseView | null>(() => this.detail().pause ?? null);

  /** Whether an **unpaused** chunk may be paused — mirrors the hub `PauseService`'s
   * refusal (`ChunkNotPausable`) so the dock never offers a control the server would
   * answer with a 409 (issue #46), exactly as Detach shows only with a live route to
   * release (issue #42). `waiting_on_human`/`needs_human` are deliberately pausable. */
  protected readonly pausable = computed<boolean>(() => !NOT_PAUSABLE.has(this.detail().status));

  /** The chunk's live route, if any — Detach shows only while this is non-null
   * (issue #42): a chunk with no live route has nothing to release. */
  protected readonly route = computed<RouteView | null>(() => this.detail().route ?? null);

  /** Whether Complete has anything left to do (issue #294) — mirrors the hub
   * `CompleteService`'s no-op on an already-`done` chunk, so the dock withholds a
   * click that would write nothing. Every other status is completable, independent of
   * `pausable`/`route`: Complete does not hang off a live route the way Detach does. */
  protected readonly completable = computed<boolean>(() => !NOT_COMPLETABLE.has(this.detail().status));

  /** Whether Delete reaches this chunk's status ({@link UNACQUIRED_STATUSES}, D8,
   * issue #364) — a chunk with an acquiring runner has no live route to release,
   * so Delete never offers a click the hub would refuse. */
  protected readonly deletable = computed<boolean>(() => UNACQUIRED_STATUSES.has(this.detail().status));

  /** Every prerequisite this chunk still waits on — `neighborhood.prerequisites` minus
   * the satisfied ones, which by definition block nothing. Unlike `blocked`, which names
   * one representative, this is the whole set the header line spells out. */
  protected readonly blockedBy = computed<readonly string[]>(() =>
    (this.detail().neighborhood?.prerequisites ?? []).filter((n) => !n.satisfied).map((n) => n.chunk_id),
  );

  /** Every chunk this one still holds up — `neighborhood.dependents` minus the satisfied
   * ones. A dependent edge's `satisfied` tracks the *subject* chunk (D4), so a done chunk
   * correctly reports blocking nothing. */
  protected readonly blocking = computed<readonly string[]>(() =>
    (this.detail().neighborhood?.dependents ?? []).filter((n) => !n.satisfied).map((n) => n.chunk_id),
  );

  /** A neighbor's compact ref — every surface that names an entity compactly resolves
   * through {@link compactRef} (`compact-ref.ts`). */
  protected shortId(chunkId: string): string {
    return compactRef(chunkId);
  }

  /** Open a confirmation before emitting `detach` for the container's mutation to fire. */
  protected onDetach(): void {
    if (!this.route()) return;
    const chunkId = this.detail().chunk_id;
    this.pendingConfirm.set({
      heading: `Detach chunk ${chunkId}`,
      message: `Detach chunk ${chunkId} from its runner? This releases the runner; ` +
        `the chunk keeps its current status (this is not requeue).`,
      confirmLabel: 'Detach',
      variant: 'primary',
      run: () => this.detach.emit(chunkId),
    });
  }

  /** Open a confirmation before emitting `pauseChunk` for the container's mutation to fire (issue #46). */
  protected onPause(): void {
    if (this.pause() || !this.pausable()) return;
    const chunkId = this.detail().chunk_id;
    this.pendingConfirm.set({
      heading: `Pause chunk ${chunkId}`,
      message: `Pause chunk ${chunkId}? This kills its active worker but keeps the ` +
        `claim (this is not detach); resume it later to pick the work back up.`,
      confirmLabel: 'Pause',
      variant: 'primary',
      run: () => this.pauseChunk.emit(chunkId),
    });
  }

  /** Open a confirmation before emitting `resumeChunk` for the container's mutation to fire (issue #46).
   * Guarded on the pause **fact**, never on `status`. */
  protected onResume(): void {
    if (!this.pause()) return;
    const chunkId = this.detail().chunk_id;
    this.pendingConfirm.set({
      heading: `Resume chunk ${chunkId}`,
      message: `Resume chunk ${chunkId}? Its runner picks the work back up from ` +
        `where the pause stopped it.`,
      confirmLabel: 'Resume',
      variant: 'primary',
      run: () => this.resumeChunk.emit(chunkId),
    });
  }

  /** Open a confirmation before emitting `complete` for the container's mutation to fire (issue #294).
   * Unlike Detach/Pause/Resume, this is a one-way door: there is no un-complete verb,
   * and the confirmation says so. */
  protected onComplete(): void {
    if (!this.completable()) return;
    const chunkId = this.detail().chunk_id;
    this.pendingConfirm.set({
      heading: `Complete chunk ${chunkId}`,
      message: `Complete chunk ${chunkId}? This marks it done by hand; there is no ` + `un-complete verb.`,
      confirmLabel: 'Complete',
      variant: 'danger',
      run: () => this.complete.emit(chunkId),
    });
  }

  /** Open a confirmation before emitting `delete` for the container's mutation to fire (D8, issue
   * #364). Withdraws the chunk's hub item(s); there is no undo. */
  protected onDelete(): void {
    if (!this.deletable()) return;
    const chunkId = this.detail().chunk_id;
    this.pendingConfirm.set({
      heading: `Delete chunk ${chunkId}`,
      message: `Delete chunk ${chunkId}? This withdraws its hub item(s); there is no undo.`,
      confirmLabel: 'Delete',
      variant: 'danger',
      run: () => this.delete.emit(chunkId),
    });
  }

  protected onConfirmed(): void {
    const pending = this.pendingConfirm();
    this.pendingConfirm.set(null);
    pending?.run();
  }

  protected onCancelled(): void {
    this.pendingConfirm.set(null);
  }
}
