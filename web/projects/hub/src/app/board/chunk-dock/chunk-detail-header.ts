import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { type ChunkDetail, type ChunkStatus, type PauseView, type WorkRefView, type RouteView, compactRef, runnerDisplayName, runnerTitle, KitButton, KitConfirmDialog, type KitConfirmDialogPrompt, KitMenu, KitMenuPanel, KitMenuItem, KitMenuItemSubtitle, KitTooltip, completeCopy, deleteCopy, detachCopy, pauseCopy, resumeCopy } from 'fleet';

/**
 * The chunk detail dock's header — the chunk's identity (short name, work item, state,
 * and current node) plus its operator actions: Detach, Pause/Resume, Complete, Delete,
 * and dismiss.
 *
 * Presentational only: it holds the detail input and emits `dismiss`, `detach`,
 * `pauseChunk`, `resumeChunk`, `complete`, and `delete`. The status chip renders
 * {@link renderedStatus} rather than `detail().status` (`bzh:frontend-pending-override`).
 */
@Component({
  selector: 'app-chunk-detail-header',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton, KitConfirmDialog, KitMenu, KitMenuItem, KitMenuItemSubtitle, KitMenuPanel, KitTooltip, RouterLink],
  templateUrl: './chunk-detail-header.html',
  styleUrl: './chunk-detail-header.css',
})
export class ChunkDetailHeader {
  /** The chunk aggregate to render (identity, status, current node, pause, route). */
  readonly detail = input.required<ChunkDetail>();

  /** The chunk's status as the status chip renders it (`bzh:frontend-pending-override`).
   * Never consulted by
   * {@link pausable}/{@link completable}/{@link deletable}: those gate what the *next*
   * click is admissible to fire against the server-read status, which an in-flight
   * mutation's own predicted outcome must not perturb. */
  readonly renderedStatus = input.required<ChunkStatus>();

  /** Whether the current identity may operate Pause/Resume/Detach (`chunk:control`);
   * withholds those controls when `false`. */
  readonly canControl = input(false);

  /** The chunk detail route's path segments, before the chunk id — the longname link's target. */
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

  /** Emitted with the chunk id when the operator confirms Detach. */
  readonly detach = output<string>();

  /** Emitted with the chunk id when the operator confirms Pause. */
  readonly pauseChunk = output<string>();

  /** Emitted with the chunk id when the operator confirms Resume. */
  readonly resumeChunk = output<string>();

  /** Emitted with the chunk id when the operator confirms Complete. */
  readonly complete = output<string>();

  /** Emitted with the chunk id when the operator confirms Delete. */
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

  /** The chunk's open operator pause, if any — who set it. Read off the
   * detail's `pause` fact, not `status`: a chunk both paused and parked on a question
   * derives `waiting_on_human`, so `status` alone would never surface it.
   *
   * This is also the **Pause/Resume switch**: non-null renders Resume, null renders
   * Pause (subject to {@link pausable}). `status` must never gate Resume. */
  protected readonly pause = computed<PauseView | null>(() => this.detail().pause ?? null);

  /** Whether an **unpaused** chunk may be paused — `ChunkDetail.pausable`. */
  protected readonly pausable = computed<boolean>(() => this.detail().pausable ?? false);

  /** The chunk's live route, if any — Detach shows only while this is non-null:
   * a chunk with no live route has nothing to release. */
  protected readonly route = computed<RouteView | null>(() => this.detail().route ?? null);

  /** The display name of the runner holding the route, or `null` while none does — the
   * claim line's and the Pause/Resume/Detach copy's `<runner>` slot. */
  protected readonly claimant = computed<string | null>(() => {
    const route = this.route();
    return route ? runnerDisplayName(route.runner_id, route.runner_name) : null;
  });

  /** The claim line's tooltip — the display name the line may clip, then the runner's full id. */
  protected readonly claimantTitle = computed<string | null>(() => {
    const route = this.route();
    return route ? runnerTitle(route.runner_id, route.runner_name) : null;
  });

  /** The node the chunk currently sits at, for display and for `detachCopy`'s own
   * `<node>` slot — the same fallback chain the `.nd` chip already reads
   * (`current_node_name`, then `current_node_id`, then an em dash). */
  protected readonly currentNodeName = computed<string>(
    () => this.detail().current_node_name ?? this.detail().current_node_id ?? '—',
  );

  /** Whether Complete is offered — `ChunkDetail.completable`. */
  protected readonly completable = computed<boolean>(() => this.detail().completable ?? false);

  /** Whether Delete is offered — `ChunkDetail.deletable`. */
  protected readonly deletable = computed<boolean>(() => this.detail().deletable ?? false);

  /** Every prerequisite this chunk still waits on — `neighborhood.prerequisites` minus
   * the satisfied ones, which by definition block nothing. Unlike `blocked`, which names
   * one representative, this is the whole set the header line spells out. */
  protected readonly blockedBy = computed<readonly string[]>(() =>
    (this.detail().neighborhood?.prerequisites ?? []).filter((n) => !n.satisfied).map((n) => n.chunk_id),
  );

  /** Every chunk this one still holds up — `neighborhood.dependents` minus the satisfied
   * ones. A dependent edge's `satisfied` tracks the *subject* chunk, so a done chunk
   * correctly reports blocking nothing. */
  protected readonly blocking = computed<readonly string[]>(() =>
    (this.detail().neighborhood?.dependents ?? []).filter((n) => !n.satisfied).map((n) => n.chunk_id),
  );

  /** Whether Delete is withheld — {@link deletable}'s own status gate, plus
   * {@link blocking}: Delete is only ever offered at `not_ready`/`ready`, never
   * `done`, so every entry `blocking()` already filters to is provably still
   * unsatisfied — no fresh read of `neighborhood.dependents` is needed here. */
  protected readonly deleteDisabled = computed<boolean>(() => !this.deletable() || this.blocking().length > 0);

  /** Delete's menu subtitle — names the dependents still holding it back when
   * {@link blocking} is non-empty, falling back to `deleteCopy()`'s own subtitle
   * otherwise. */
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

  /** Open a confirmation, emitting `detach` once confirmed. */
  protected onDetach(): void {
    const route = this.route();
    if (!route) return;
    const chunkId = this.detail().chunk_id;
    this.pendingConfirm.set({
      heading: `Detach chunk ${chunkId}`,
      message: detachCopy(runnerDisplayName(route.runner_id, route.runner_name), this.currentNodeName()).text,
      confirmLabel: 'Detach',
      variant: 'primary',
      run: () => this.detach.emit(chunkId),
    });
  }

  /** Open a confirmation, emitting `pauseChunk` once confirmed. */
  protected onPause(): void {
    if (this.pause() || !this.pausable()) return;
    const chunkId = this.detail().chunk_id;
    this.pendingConfirm.set({
      heading: `Pause chunk ${chunkId}`,
      message: pauseCopy(this.claimant()).text,
      confirmLabel: 'Pause',
      variant: 'primary',
      run: () => this.pauseChunk.emit(chunkId),
    });
  }

  /** Open a confirmation, emitting `resumeChunk` once confirmed. Guarded on the pause
   * **fact**, never on `status`. */
  protected onResume(): void {
    if (!this.pause()) return;
    const chunkId = this.detail().chunk_id;
    this.pendingConfirm.set({
      heading: `Resume chunk ${chunkId}`,
      message: resumeCopy(this.claimant()).text,
      confirmLabel: 'Resume',
      variant: 'primary',
      run: () => this.resumeChunk.emit(chunkId),
    });
  }

  /** Open a confirmation, emitting `complete` once confirmed. */
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

  /** Open a confirmation, emitting `delete` once confirmed. */
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
