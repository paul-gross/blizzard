import { ChangeDetectionStrategy, Component, input, output, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import {
  KitBadge,
  KitButton,
  KitConfirmDialog,
  type KitConfirmDialogPrompt,
  KitTooltip,
  pauseCopy,
  resumeCopy,
  type runnerApi,
  type Tone,
} from 'fleet';

/**
 * The machine detail dock's header — the full chunk id linked to the chunk detail
 * route, its work items as links, the derived state, a Pause/Resume on the
 * `bzh:claim-vocabulary` copy and tooltip, and a close button. The chunk link
 * carries the chunk in the route's path and no query params.
 *
 * Presentational (`bzh:frontend-container-presentational`): it renders its inputs
 * and asks for confirmation before emitting {@link pauseChunk}/{@link resumeChunk}.
 */
@Component({
  selector: 'app-machine-detail-header',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBadge, KitButton, KitConfirmDialog, KitTooltip, RouterLink],
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

  /** The newest attempt's node name + epoch, alongside the status text. */
  readonly nodeName = input<string>('');
  readonly epoch = input<number>(0);

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
