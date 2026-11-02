import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';

import { ChunkDeleteRefusal, type ChunkDetail, type PauseView, type RouteView, type WorkRefView } from '../../api/hub';
import { compactRef } from '../../core/compact-ref';
import { completeCopy, deleteCopy, detachCopy, pauseCopy, resumeCopy } from '../chunk-detail/chunk-action-copy';
import { KitConfirmDialog, type KitConfirmDialogPrompt } from '../../kit/kit-confirm-dialog';
import { KitMenu, KitMenuPanel } from '../../kit/kit-menu';
import { KitMenuItem, KitMenuItemSubtitle } from '../../kit/kit-menu-item';
import { injectRunnerHostNames, runnerDisplayName, runnerTitle } from '../../core/runner-display-name';
import { KitSwatch } from '../../kit/kit-swatch';
import { toneColor } from '../../kit/kit-badge';
import type { Tone } from '../../kit/tone';
import { WorkRefLink } from '../work-ref-link/work-ref-link';

/**
 * The chunk-detail-page identity header — the chunk's own id beside its
 * derived-status badge. Presentational, no injection: an input for the id,
 * the status text, and the {@link Tone} it colors the badge by.
 *
 * Renders the **full** `ch_…` id, not `compactRef`'s short form
 * (`compact-ref.ts`). That id is a single unbroken token, so `.cid` allows a
 * mid-token break (`overflow-wrap: anywhere`, with `min-width: 0` so the flex
 * item can shrink) rather than widening a phone-width page.
 */
@Component({
  selector: 'fleet-chunk-page-header',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [WorkRefLink, KitSwatch, KitConfirmDialog, KitMenu, KitMenuItem, KitMenuItemSubtitle, KitMenuPanel],
  templateUrl: './chunk-page-header.html',
  styleUrl: './chunk-page-header.css',
})
export class ChunkPageHeader {
  /** The chunk's full `ch_…` id — never `compactRef`'d here. */
  readonly chunkId = input.required<string>();

  /** The chunk's current status, rendered verbatim on the badge. */
  readonly status = input.required<string>();

  /** The derived {@link Tone} the status text colors by — resolved by the caller
   * before this component ever sees the status (`bzh:frontend-formatters`). */
  readonly tone = input.required<Tone>();

  /** The work items this chunk serves, rendered first on the line. */
  readonly pointers = input<readonly WorkRefView[]>([]);

  /** The chunk's project name, leading the line; `null` omits the project fact. */
  readonly projectName = input<string | null>(null);

  /** The project's swatch color; `null` draws the name alone. */
  readonly projectColor = input<string | null>(null);

  /** The chunk's live route, or `null` while no runner holds it. */
  readonly route = input<RouteView | null>(null);

  /** Each runner's host name by runner id — empty where the app provides no registry,
   * so the runner keeps the compact-id form. */
  private readonly runnerHosts = injectRunnerHostNames();

  /** The runner holding the route as `host/runner`, or `null` while none does. */
  protected readonly claimant = computed<string | null>(() => {
    const route = this.route();
    return route ? runnerDisplayName(route.runner_id, route.runner_name, this.runnerHosts().get(route.runner_id)) : null;
  });

  protected readonly claimantTitle = computed<string | null>(() => {
    const route = this.route();
    return route ? runnerTitle(route.runner_id, route.runner_name, this.runnerHosts().get(route.runner_id)) : null;
  });

  /** The status text's own color, resolved off {@link tone} (`kit-badge.ts` owns the
   * mapping). */
  protected readonly statusColor = computed<string>(() => toneColor(this.tone()));

  /** The chunk aggregate the actions menu gates on; `null` hides the menu. */
  readonly detail = input<ChunkDetail | null>(null);

  /** Whether the daemon supports chunk control at all and the identity may use it
   * (`chunk:control`); `false` hides the ⋮ menu, as in the runner app. */
  readonly canControl = input(false);

  readonly pausePending = input(false);
  readonly detachPending = input(false);
  readonly completePending = input(false);
  readonly deletePending = input(false);

  readonly detach = output<string>();
  readonly pauseChunk = output<string>();
  readonly resumeChunk = output<string>();
  readonly complete = output<string>();
  readonly delete = output<string>();

  protected readonly pauseCopy = pauseCopy;
  protected readonly resumeCopy = resumeCopy;
  protected readonly detachCopy = detachCopy;
  protected readonly completeCopy = completeCopy;
  protected readonly deleteCopy = deleteCopy;

  protected readonly pendingConfirm = signal<(KitConfirmDialogPrompt & { readonly run: () => void }) | null>(null);

  protected readonly menuVisible = computed(() => this.canControl() && this.detail() !== null);

  /** The open operator pause — the Pause/Resume switch; `status` never gates Resume. */
  protected readonly pause = computed<PauseView | null>(() => this.detail()?.pause ?? null);
  protected readonly pausable = computed<boolean>(() => this.detail()?.pausable ?? false);
  protected readonly completable = computed<boolean>(() => this.detail()?.completable ?? false);
  protected readonly currentNodeName = computed<string>(
    () => this.detail()?.current_node_name ?? this.detail()?.current_node_id ?? '—',
  );
  private readonly deleteRefusal = computed(() => this.detail()?.delete_refusal ?? null);
  protected readonly deleteDisabled = computed<boolean>(() => this.deleteRefusal() !== null);

  /** Delete's subtitle — why the hub refuses it, else `deleteCopy()`'s own. */
  protected readonly deleteSubtitle = computed<string>(() => {
    switch (this.deleteRefusal()) {
      case ChunkDeleteRefusal.STATUS:
        return 'Only before a runner claims it';
      case ChunkDeleteRefusal.DEPENDENTS: {
        const ids = (this.detail()?.neighborhood?.dependents ?? []).filter((n) => !n.satisfied).map((n) => compactRef(n.chunk_id));
        return `Blocked: ${ids.join(', ')} depend on this`;
      }
      default:
        return deleteCopy().subtitle ?? '';
    }
  });

  protected onDetach(): void {
    const route = this.route();
    if (!route) return;
    const chunkId = this.chunkId();
    this.confirm(`Detach chunk ${chunkId}`, detachCopy(this.claimant() ?? compactRef(route.runner_id), this.currentNodeName()).text, 'Detach', 'primary', () => this.detach.emit(chunkId));
  }

  protected onPause(): void {
    if (this.pause() || !this.pausable()) return;
    const chunkId = this.chunkId();
    this.confirm(`Pause chunk ${chunkId}`, pauseCopy(this.claimant()).text, 'Pause', 'primary', () => this.pauseChunk.emit(chunkId));
  }

  protected onResume(): void {
    if (!this.pause()) return;
    const chunkId = this.chunkId();
    this.confirm(`Resume chunk ${chunkId}`, resumeCopy(this.claimant()).text, 'Resume', 'primary', () => this.resumeChunk.emit(chunkId));
  }

  protected onComplete(): void {
    if (!this.completable()) return;
    const chunkId = this.chunkId();
    this.confirm(`Complete chunk ${chunkId}`, completeCopy().text, 'Complete', 'danger', () => this.complete.emit(chunkId));
  }

  protected onDelete(): void {
    if (this.deleteDisabled()) return;
    const chunkId = this.chunkId();
    this.confirm(`Delete chunk ${chunkId}`, deleteCopy().text, 'Delete', 'danger', () => this.delete.emit(chunkId));
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
