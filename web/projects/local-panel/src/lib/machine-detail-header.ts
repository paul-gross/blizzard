import { ChangeDetectionStrategy, Component, input, output, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import {
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
 * The machine detail dock's header (issue #185) — the full chunk id, its work
 * items as links, the derived state, a working Pause/Resume on the same
 * `bzh:claim-vocabulary` copy and tooltip the hub board's own header uses
 * (`fleet/chunk-detail/chunk-detail-header.ts`), and a close button. The two
 * headers are structurally independent, not one shared model: the hub header
 * additionally carries a `⋯` overflow menu (Detach, Complete, Delete) this one
 * does not, Detach being a hub-side concern out of scope here.
 *
 * The chunk id itself links to the runner-local
 * chunk detail route (issue #318) — the operator's way into the shared
 * `fleet` sections and the transcript, both of which moved out of this dock.
 * The link carries the chunk in the route's own path and no query params at
 * all: `?chunk=` is the board's selection (the shared `injectChunkUrlSelection`) and means
 * nothing on the detail route, and `?attempt=` is that route's own, written
 * there once an attempt is picked.
 *
 * Presentational (`bzh:frontend-container-presentational`): {@link MachineDetail}
 * owns the severable `ChunkDetail` read and the pause mutation, and forwards
 * their data down as plain inputs; this component only renders and, mirroring the
 * hub header's own `onPause`/`onResume`, asks for confirmation before emitting
 * {@link pauseChunk}/{@link resumeChunk} upward.
 */
@Component({
  selector: 'local-machine-detail-header',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton, KitConfirmDialog, KitTooltip, RouterLink],
  templateUrl: './machine-detail-header.html',
  styleUrl: './machine-detail-header.css',
})
export class MachineDetailHeader {
  /** The selected chunk's full id — never the compact shortname (issue #185). */
  readonly chunkId = input.required<string>();

  /** This runner's own id, for {@link pauseCopy}/{@link resumeCopy}'s `<runner>`
   * slot — container-fed off the dashboard read (`bzh:claim-vocabulary`); `null`
   * before that read resolves degrades to the copy table's unclaimed phrasing. */
  readonly runnerName = input<string | null>(null);

  /** The chunk detail route's own path segments, before the chunk id — mirrors
   * `fleet`'s `ChunkArtifacts`/`ChunkDetailHeader` `linkBase` (`bzh:frontend-kit-floor`)
   * so this component doesn't independently hardcode the route it links to. */
  readonly linkBase = input<readonly string[]>(['/board', 'chunk']);

  /** The chunk's work refs — each linked out to its source's web address when the
   * configured binding rendered one (a null `web_url` degrades to plain text, no
   * broken link). The header's own severable enrichment, container-fed. */
  readonly workRefs = input<readonly runnerApi.WorkRefView[]>([]);

  /** The derived machine-side status label/tone (container-folded). */
  readonly statusLabel = input<string | null>(null);
  readonly statusTone = input<Tone | undefined>(undefined);

  /** The newest attempt's node name + epoch, alongside the status text. */
  readonly nodeName = input<string>('');
  readonly epoch = input<number>(0);

  /** The chunk's open operator pause, if any — non-null renders Resume, null
   * renders Pause (subject to {@link pausable}). Container-fed off the fresh
   * `ChunkDetail.pause`, never the machine-derived status. */
  readonly pause = input<runnerApi.PauseView | null>(null);

  /** Whether an **unpaused** chunk may be paused — container-folded off the
   * fresh `ChunkDetail.status` (mirrors the hub `PauseService`'s refusal). */
  readonly pausable = input<boolean>(false);

  /** Whether the container's Pause/Resume mutation is in flight — disables
   * both buttons for the duration so a double click can't fire the request
   * twice while the first still settles (`bzh:frontend-pending-override`,
   * mirrors `fleet/chunk-detail/chunk-detail-header.ts`'s own `pausePending`). */
  readonly pending = input<boolean>(false);

  /** Emitted when the operator dismisses the dock via its close button. */
  readonly dismiss = output<void>();

  /** Emitted with the chunk id once the operator confirms Pause — the container's
   * mutation fires off this. */
  readonly pauseChunk = output<string>();

  /** Emitted with the chunk id once the operator confirms Resume. */
  readonly resumeChunk = output<string>();

  protected readonly pendingConfirm = signal<(KitConfirmDialogPrompt & { readonly run: () => void }) | null>(null);

  /** {@link pauseCopy}/{@link resumeCopy} bound onto the protected instance so the
   * template can call each directly (`bzh:claim-vocabulary`, `chunk-action-copy.ts`). */
  protected readonly pauseCopy = pauseCopy;
  protected readonly resumeCopy = resumeCopy;

  /** Open a confirmation before emitting {@link pauseChunk} — mirrors the hub
   * header's own `onPause`. The confirm copy is `pauseCopy`'s own `text`. */
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

  /** Open a confirmation before emitting {@link resumeChunk} — mirrors the hub
   * header's own `onResume`. The confirm copy is `resumeCopy`'s own `text`. */
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
