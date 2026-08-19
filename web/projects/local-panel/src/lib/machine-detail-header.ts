import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { RouterLink } from '@angular/router';
import { KitButton, type runnerApi, type Tone } from 'fleet';

/**
 * The machine detail dock's header (issue #185) — matches the hub board's own
 * chunk-detail header shape (`fleet/chunk-detail/chunk-detail-header.ts`, the
 * model): the full chunk id, its work items as links, the derived state, a
 * working Pause/Resume, and a close button. Detach is deliberately omitted —
 * it is a hub-side concern. The chunk id itself links to the runner-local
 * chunk detail route (issue #318) — the operator's way into the shared
 * `fleet` sections and the transcript, both of which moved out of this dock.
 * The link carries the chunk in the route's own path and no query params at
 * all: `?chunk=` is the board's selection (`panel-selection.ts`) and means
 * nothing on the detail route, and `?attempt=` is that route's own, written
 * there once an attempt is picked.
 *
 * Presentational (`bzh:frontend-container-presentational`): {@link MachineDetail}
 * owns the severable `ChunkDetail` read and the pause mutation, and forwards
 * their data down as plain inputs; this component only renders and, mirroring the
 * hub header's own `onPause`/`onResume`, guards the mutating verbs behind a
 * `confirm()` before emitting {@link pauseChunk}/{@link resumeChunk} upward.
 */
@Component({
  selector: 'local-machine-detail-header',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton, RouterLink],
  templateUrl: './machine-detail-header.html',
  styleUrl: './machine-detail-header.css',
})
export class MachineDetailHeader {
  /** The selected chunk's full id — never the compact shortname (issue #185). */
  readonly chunkId = input.required<string>();

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

  /** Emitted when the operator dismisses the dock via its close button. */
  readonly dismiss = output<void>();

  /** Emitted with the chunk id once the operator confirms Pause — the container's
   * mutation fires off this. */
  readonly pauseChunk = output<string>();

  /** Emitted with the chunk id once the operator confirms Resume. */
  readonly resumeChunk = output<string>();

  /** Confirm, then emit {@link pauseChunk} — mirrors the hub header's own `onPause`. */
  protected onPause(): void {
    if (this.pause() || !this.pausable()) return;
    const confirmed = globalThis.confirm(
      `Pause chunk ${this.chunkId()}? This kills its active worker but keeps the claim ` +
        `(this is not detach); resume it later to pick the work back up.`,
    );
    if (!confirmed) return;
    this.pauseChunk.emit(this.chunkId());
  }

  /** Confirm, then emit {@link resumeChunk} — mirrors the hub header's own `onResume`. */
  protected onResume(): void {
    if (!this.pause()) return;
    const confirmed = globalThis.confirm(
      `Resume chunk ${this.chunkId()}? Its runner picks the work back up from where the ` +
        `pause stopped it.`,
    );
    if (!confirmed) return;
    this.resumeChunk.emit(this.chunkId());
  }
}
