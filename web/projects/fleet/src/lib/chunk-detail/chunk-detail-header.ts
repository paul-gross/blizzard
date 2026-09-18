import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import type { ChunkDetail, ChunkStatus, PauseView, WorkRefView, RouteView } from '../api/hub';
import { compactRef } from '../compact-ref';
import { KitButton } from '../kit/kit-button';
import { KitConfirmDialog, type KitConfirmDialogPrompt } from '../kit/kit-confirm-dialog';
import { KitMenu, KitMenuPanel } from '../kit/kit-menu';
import { KitMenuItem, KitMenuItemSubtitle } from '../kit/kit-menu-item';
import { KitTooltip } from '../kit/kit-tooltip';
import { completeCopy, deleteCopy, detachCopy, pauseCopy, resumeCopy } from './chunk-action-copy';

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
 * mutations those events drive live in the container. The status chip renders
 * {@link renderedStatus} rather than `detail().status` directly, so a pending Pause or
 * Complete can show its predicted outcome before the server confirms it
 * (`bzh:frontend-pending-override`) — the merge itself is the container's
 * (`chunk-detail.ts`'s `overrideStatus`/`renderedStatus`), which names which of the
 * four controls that covers and why the other two do not qualify.
 */
@Component({
  selector: 'fleet-chunk-detail-header',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton, KitConfirmDialog, KitMenu, KitMenuItem, KitMenuItemSubtitle, KitMenuPanel, KitTooltip, RouterLink],
  templateUrl: './chunk-detail-header.html',
  styleUrl: './chunk-detail-header.css',
})
export class ChunkDetailHeader {
  /** The chunk aggregate to render (identity, status, current node, pause, route). */
  readonly detail = input.required<ChunkDetail>();

  /** The chunk's status as the header's own status chip renders it — the container's
   * already-applied result (`bzh:frontend-pending-override`, `chunk-detail.ts`'s
   * `overrideStatus`/`renderedStatus`): a currently pending Pause/Complete's predicted
   * outcome where one overrides, else the real `detail().status`. Never consulted by
   * {@link pausable}/{@link completable}/{@link deletable}: those gate what the *next*
   * click is admissible to fire against the server-read status, which an in-flight
   * mutation's own predicted outcome must not perturb. */
  readonly renderedStatus = input.required<ChunkStatus>();

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

  /** Whether the pause/resume mutation is in flight — disables whichever of
   * Pause/Resume is currently shown so a double click cannot fire it twice. */
  readonly pausePending = input(false);

  /** Whether the detach mutation is in flight — disables the Detach menu item. */
  readonly detachPending = input(false);

  /** Whether the complete mutation is in flight — combined with {@link completable}
   * to disable the Complete menu item. */
  readonly completePending = input(false);

  /** Whether the delete mutation is in flight — combined with {@link deleteDisabled}
   * to disable the Delete menu item. */
  readonly deletePending = input(false);

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

  protected readonly pendingConfirm = signal<(KitConfirmDialogPrompt & { readonly run: () => void }) | null>(null);

  /** The action-copy table (`bzh:claim-vocabulary`, `chunk-action-copy.ts`) — bound
   * onto the protected instance so the template can call each function directly
   * rather than this class re-declaring a per-action copy computed for every one. */
  protected readonly pauseCopy = pauseCopy;
  protected readonly resumeCopy = resumeCopy;
  protected readonly detachCopy = detachCopy;
  protected readonly completeCopy = completeCopy;
  protected readonly deleteCopy = deleteCopy;

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

  /** The node the chunk currently sits at, for display and for `detachCopy`'s own
   * `<node>` slot — the same fallback chain the `.nd` chip already reads
   * (`current_node_name`, then `current_node_id`, then an em dash). */
  protected readonly currentNodeName = computed<string>(
    () => this.detail().current_node_name ?? this.detail().current_node_id ?? '—',
  );

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

  /** Whether Delete is withheld (D6) — {@link deletable}'s own status gate, plus
   * {@link blocking}: Delete is only ever offered at `not_ready`/`ready`, never
   * `done`, so every entry `blocking()` already filters to is provably still
   * unsatisfied — no fresh read of `neighborhood.dependents` is needed here. */
  protected readonly deleteDisabled = computed<boolean>(() => !this.deletable() || this.blocking().length > 0);

  /** Delete's menu subtitle — names the dependents still holding it back when
   * {@link blocking} is non-empty, falling back to `deleteCopy()`'s own subtitle
   * otherwise (D6). */
  protected readonly deleteSubtitle = computed<string>(() => {
    const blockers = this.blocking();
    return blockers.length > 0
      ? `Blocked: ${blockers.map((id) => this.shortId(id)).join(', ')} depend on this`
      : (deleteCopy().subtitle ?? '');
  });

  /** A neighbor's compact ref — every surface that names an entity compactly resolves
   * through {@link compactRef} (`compact-ref.ts`). */
  protected shortId(chunkId: string): string {
    return compactRef(chunkId);
  }

  /** Open a confirmation before emitting `detach` for the container's mutation to fire.
   * The confirm copy is `detachCopy`'s own `text` (`bzh:claim-vocabulary`). */
  protected onDetach(): void {
    const route = this.route();
    if (!route) return;
    const chunkId = this.detail().chunk_id;
    this.pendingConfirm.set({
      heading: `Detach chunk ${chunkId}`,
      message: detachCopy(route.runner_id, this.currentNodeName()).text,
      confirmLabel: 'Detach',
      variant: 'primary',
      run: () => this.detach.emit(chunkId),
    });
  }

  /** Open a confirmation before emitting `pauseChunk` for the container's mutation to
   * fire (issue #46). The confirm copy is `pauseCopy`'s own `text` (`bzh:claim-vocabulary`). */
  protected onPause(): void {
    if (this.pause() || !this.pausable()) return;
    const chunkId = this.detail().chunk_id;
    this.pendingConfirm.set({
      heading: `Pause chunk ${chunkId}`,
      message: pauseCopy(this.route()?.runner_id ?? null).text,
      confirmLabel: 'Pause',
      variant: 'primary',
      run: () => this.pauseChunk.emit(chunkId),
    });
  }

  /** Open a confirmation before emitting `resumeChunk` for the container's mutation to
   * fire (issue #46). Guarded on the pause **fact**, never on `status`. The confirm
   * copy is `resumeCopy`'s own `text` (`bzh:claim-vocabulary`). */
  protected onResume(): void {
    if (!this.pause()) return;
    const chunkId = this.detail().chunk_id;
    this.pendingConfirm.set({
      heading: `Resume chunk ${chunkId}`,
      message: resumeCopy(this.route()?.runner_id ?? null).text,
      confirmLabel: 'Resume',
      variant: 'primary',
      run: () => this.resumeChunk.emit(chunkId),
    });
  }

  /** Open a confirmation before emitting `complete` for the container's mutation to fire
   * (issue #294). Unlike Detach/Pause/Resume, this is a one-way door — `completeCopy`'s
   * own `text` (`bzh:claim-vocabulary`) says so. */
  protected onComplete(): void {
    if (!this.completable()) return;
    const chunkId = this.detail().chunk_id;
    this.pendingConfirm.set({
      heading: `Complete chunk ${chunkId}`,
      message: completeCopy().text,
      confirmLabel: 'Complete',
      variant: 'danger',
      run: () => this.complete.emit(chunkId),
    });
  }

  /** Open a confirmation before emitting `delete` for the container's mutation to fire
   * (D8, issue #364). `deleteCopy`'s own `text` (`bzh:claim-vocabulary`) says there is
   * no undo. */
  protected onDelete(): void {
    if (this.deleteDisabled()) return;
    const chunkId = this.detail().chunk_id;
    this.pendingConfirm.set({
      heading: `Delete chunk ${chunkId}`,
      message: deleteCopy().text,
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
