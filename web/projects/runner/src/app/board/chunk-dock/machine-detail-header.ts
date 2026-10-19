import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import {
  KitBadge,
  KitButton,
  KitConfirmDialog,
  type KitConfirmDialogPrompt,
  KitMenu,
  KitMenuItem,
  KitMenuItemSubtitle,
  KitMenuPanel,
  formatHeldFor,
  injectNowSignal,
  pauseCopy,
  resumeCopy,
  type runnerApi,
  type Tone,
  WorkRefLink,
} from 'fleet';

import { sessionEnding } from '../../core/worker-session';
import { WorkerLine } from '../../machine/worker-line';

/**
 * The machine detail dock's header, shaped like the hub dock's — the full chunk id linked to
 * the chunk detail route, then the identity line in the hub's order (project · work items ·
 * `host/runner` · status · node), the chunk's worker under it ({@link WorkerLine} and its pid,
 * or how the last session ended when none is live), and on the right a ⋯ menu holding
 * Pause/Resume on the `bzh:claim-vocabulary` copy, then a close button.
 *
 * Presentational (`bzh:frontend-container-presentational`): it renders its inputs
 * and asks for confirmation before emitting {@link pauseChunk}/{@link resumeChunk}.
 */
@Component({
  selector: 'app-machine-detail-header',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [WorkRefLink, KitBadge, KitButton, KitConfirmDialog, KitMenu, KitMenuItem, KitMenuItemSubtitle, KitMenuPanel, RouterLink, WorkerLine],
  templateUrl: './machine-detail-header.html',
  styleUrl: './machine-detail-header.css',
})
export class MachineDetailHeader {
  /** The selected chunk's full id — never the compact shortname. */
  readonly chunkId = input.required<string>();

  /** This runner's own id, for {@link pauseCopy}/{@link resumeCopy}'s `<runner>`
   * slot (`bzh:claim-vocabulary`); `null` degrades to the copy table's unclaimed phrasing. */
  readonly runnerName = input<string | null>(null);

  /** The chunk detail route's path segments, before the chunk id (`bzh:frontend-kit-floor`). */
  readonly linkBase = input<readonly string[]>(['/board', 'chunk']);

  /** The chunk's work refs — each linked to its source's web address, or plain text
   * when `web_url` is null. */
  readonly workRefs = input<readonly runnerApi.WorkRefView[]>([]);

  /** The derived machine-side status label and tone. */
  readonly statusLabel = input<string | null>(null);
  readonly statusTone = input<Tone | undefined>(undefined);

  /** The node the chunk's newest session ran. */
  readonly nodeName = input<string>('');

  /** The project the chunk is attributed to, by name, or `null`. */
  readonly projectName = input<string | null>(null);

  /** The chunk's newest lease — its worker while live, else the session that ended last. */
  readonly lease = input<runnerApi.LeaseView | null>(null);

  /** The live worker, or `null` once the newest session ended. */
  readonly worker = input<runnerApi.LeaseView | null>(null);

  /** Ticks once a second so the ended-ago note advances. */
  private readonly now = injectNowSignal();

  /** With no live worker, how long ago the last session ended and how. */
  protected readonly noWorkerText = computed<string>(() => {
    const last = this.lease();
    if (!last) return 'No worker';
    const ending = sessionEnding(last);
    const endedAt = last.closed_at ?? last.last_heartbeat_at;
    const ago = endedAt ? ` ${formatHeldFor(this.now() - Date.parse(endedAt))} ago` : '';
    const how = ending === null ? '' : ` · ${ending === 'exited' ? 'worker exited' : ending}`;
    return `No live worker — last session ended${ago}${how}`;
  });

  /** The chunk's open operator pause, if any — non-null renders Resume, null renders
   * Pause (subject to {@link pausable}). */
  readonly pause = input<runnerApi.PauseView | null>(null);

  /** Whether an **unpaused** chunk may be paused. */
  readonly pausable = input<boolean>(false);

  /** Whether a Pause/Resume is in flight — disables both buttons so a double click
   * can't fire the request twice (`bzh:frontend-pending-override`). */
  readonly pending = input<boolean>(false);

  /** Emitted when the operator dismisses the dock via its close button. */
  readonly dismiss = output<void>();

  /** Emitted with the chunk id once the operator confirms Pause. */
  readonly pauseChunk = output<string>();

  /** Emitted with the chunk id once the operator confirms Resume. */
  readonly resumeChunk = output<string>();

  protected readonly pendingConfirm = signal<(KitConfirmDialogPrompt & { readonly run: () => void }) | null>(null);

  /** {@link pauseCopy}/{@link resumeCopy} bound onto the protected instance so the
   * template can call each directly (`bzh:claim-vocabulary`, `chunk-action-copy.ts`). */
  protected readonly pauseCopy = pauseCopy;
  protected readonly resumeCopy = resumeCopy;

  /** Open a confirmation, with `pauseCopy`'s `text`, and emit {@link pauseChunk} once confirmed. */
  protected onPause(): void {
    if (this.pause() || !this.pausable()) return;
    const chunkId = this.chunkId();
    this.pendingConfirm.set({
      heading: `Pause chunk ${chunkId}`,
      message: pauseCopy(this.runnerName()).text,
      confirmLabel: 'Pause',
      variant: 'primary',
      run: () => this.pauseChunk.emit(chunkId),
    });
  }

  /** Open a confirmation, with `resumeCopy`'s `text`, and emit {@link resumeChunk} once confirmed. */
  protected onResume(): void {
    if (!this.pause()) return;
    const chunkId = this.chunkId();
    this.pendingConfirm.set({
      heading: `Resume chunk ${chunkId}`,
      message: resumeCopy(this.runnerName()).text,
      confirmLabel: 'Resume',
      variant: 'primary',
      run: () => this.resumeChunk.emit(chunkId),
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
