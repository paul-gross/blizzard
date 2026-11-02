import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';

import { ChunkDeleteRefusal, type ChunkDetail, type PauseView } from '../../api/hub';
import { compactRef } from '../../core/compact-ref';
import { KitConfirmDialog, type KitConfirmDialogPrompt } from '../../kit/kit-confirm-dialog';
import { KitMenu, KitMenuPanel } from '../../kit/kit-menu';
import { KitMenuItem, KitMenuItemSubtitle } from '../../kit/kit-menu-item';
import { completeCopy, deleteCopy, detachCopy, pauseCopy, resumeCopy } from './chunk-action-copy';
import type { ChunkControl } from './chunk-control';

/**
 * A chunk header's ⋮ actions menu and the confirmation each action opens — Pause/Resume,
 * Detach, then Complete and Delete — firing through the daemon's {@link ChunkControl}
 * port once confirmed. Renders nothing while the port withholds `chunk:control`.
 *
 * Every gate reads the server's own facts on {@link detail} (`pause`, `pausable`,
 * `completable`, `delete_refusal`), never a pending-override status: they decide what
 * the *next* click may fire.
 */
@Component({
  selector: 'fleet-chunk-actions-menu',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitConfirmDialog, KitMenu, KitMenuItem, KitMenuItemSubtitle, KitMenuPanel],
  templateUrl: './chunk-actions-menu.html',
  styleUrl: './chunk-actions-menu.css',
})
export class ChunkActionsMenu {
  /** The chunk aggregate the menu gates on. */
  readonly detail = input.required<ChunkDetail>();

  /** The daemon's chunk-control port the confirmed actions fire through. */
  readonly control = input.required<ChunkControl>();

  /** The runner holding the route as its display name, or `null` while none does — the
   * Pause/Resume/Detach copy's `<runner>` slot; Detach shows only while it is non-null. */
  readonly claimant = input<string | null>(null);

  /** Run once the hub confirms a delete — the host's way off a chunk that is gone. */
  readonly afterDelete = input<() => void>(() => undefined);

  /** The action-copy table (`bzh:claim-vocabulary`), bound for the template. */
  protected readonly pauseCopy = pauseCopy;
  protected readonly resumeCopy = resumeCopy;
  protected readonly detachCopy = detachCopy;
  protected readonly completeCopy = completeCopy;
  protected readonly deleteCopy = deleteCopy;

  protected readonly pendingConfirm = signal<(KitConfirmDialogPrompt & { readonly run: () => void }) | null>(null);

  private readonly chunkId = computed<string>(() => this.detail().chunk_id);

  protected readonly canControl = computed<boolean>(() => this.control().canControl());
  protected readonly pausePending = computed<boolean>(() => this.control().pausePending(this.chunkId()));
  protected readonly detachPending = computed<boolean>(() => this.control().detachPending(this.chunkId()));
  protected readonly completePending = computed<boolean>(() => this.control().completePending(this.chunkId()));
  protected readonly deletePending = computed<boolean>(() => this.control().deletePending(this.chunkId()));

  /** The open operator pause — the Pause/Resume switch: non-null renders Resume, null
   * renders Pause (subject to {@link pausable}). `status` never gates Resume: a chunk both
   * paused and parked on a question derives `waiting_on_human`. */
  protected readonly pause = computed<PauseView | null>(() => this.detail().pause ?? null);
  protected readonly pausable = computed<boolean>(() => this.detail().pausable ?? false);
  protected readonly completable = computed<boolean>(() => this.detail().completable ?? false);

  /** The node Detach's copy names — `current_node_name`, then `current_node_id`, then an em dash. */
  protected readonly currentNodeName = computed<string>(
    () => this.detail().current_node_name ?? this.detail().current_node_id ?? '—',
  );

  private readonly deleteRefusal = computed<ChunkDeleteRefusal | null>(() => this.detail().delete_refusal ?? null);

  /** Whether Delete is withheld — exactly when the hub reports a refusal. */
  protected readonly deleteDisabled = computed<boolean>(() => this.deleteRefusal() !== null);

  /** Delete's subtitle — why the hub refuses it (a runner has claimed the chunk, or the
   * unsatisfied dependents it names hold it), else `deleteCopy()`'s own. */
  protected readonly deleteSubtitle = computed<string>(() => {
    switch (this.deleteRefusal()) {
      case ChunkDeleteRefusal.STATUS:
        return 'Only before a runner claims it';
      case ChunkDeleteRefusal.DEPENDENTS: {
        const ids = (this.detail().neighborhood?.dependents ?? []).filter((n) => !n.satisfied).map((n) => compactRef(n.chunk_id));
        return `Blocked: ${ids.join(', ')} depend on this`;
      }
      default:
        return deleteCopy().subtitle ?? '';
    }
  });

  protected onDetach(): void {
    const route = this.detail().route;
    if (!route) return;
    const chunkId = this.chunkId();
    this.confirm(`Detach chunk ${chunkId}`, detachCopy(this.claimant() ?? compactRef(route.runner_id), this.currentNodeName()).text, 'Detach', 'primary', () => this.control().detach(chunkId));
  }

  protected onPause(): void {
    if (this.pause() || !this.pausable()) return;
    const chunkId = this.chunkId();
    this.confirm(`Pause chunk ${chunkId}`, pauseCopy(this.claimant()).text, 'Pause', 'primary', () => this.control().pause(chunkId));
  }

  /** Guarded on the pause **fact**, never on `status`. */
  protected onResume(): void {
    if (!this.pause()) return;
    const chunkId = this.chunkId();
    this.confirm(`Resume chunk ${chunkId}`, resumeCopy(this.claimant()).text, 'Resume', 'primary', () => this.control().resume(chunkId));
  }

  protected onComplete(): void {
    if (!this.completable()) return;
    const chunkId = this.chunkId();
    this.confirm(`Complete chunk ${chunkId}`, completeCopy().text, 'Complete', 'danger', () => this.control().complete(chunkId));
  }

  protected onDelete(): void {
    if (this.deleteDisabled()) return;
    const chunkId = this.chunkId();
    const afterDelete = this.afterDelete();
    this.confirm(`Delete chunk ${chunkId}`, deleteCopy().text, 'Delete', 'danger', () => this.control().delete(chunkId, afterDelete));
  }

  private confirm(heading: string, message: string, confirmLabel: string, variant: KitConfirmDialogPrompt['variant'], run: () => void): void {
    this.pendingConfirm.set({ heading, message, confirmLabel, variant, run });
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
