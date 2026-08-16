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
 *
 * Presentational (`bzh:frontend-container-presentational`): {@link MachineDetail}
 * owns the severable `ChunkDetailView` read and the pause mutation, and forwards
 * their data down as plain inputs; this component only renders and, mirroring the
 * hub header's own `onPause`/`onResume`, guards the mutating verbs behind a
 * `confirm()` before emitting {@link pauseChunk}/{@link resumeChunk} upward.
 */
@Component({
  selector: 'local-machine-detail-header',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton, RouterLink],
  template: `
    <header class="d-hdr">
      <div class="d-title">
        <a
          class="cid"
          data-testid="detail-chunk-ref"
          [routerLink]="[...linkBase(), chunkId()]"
          [queryParams]="{ chunk: chunkId() }"
        >{{ chunkId() }}</a>
        <span class="d-sub">
          @for (ref of workRefs(); track ref.source + ':' + ref.ref) {
            @if (ref.web_url) {
              <a
                class="iss"
                data-testid="detail-pointer"
                [href]="ref.web_url"
                target="_blank"
                rel="noreferrer"
                [attr.title]="ref.web_url"
              >{{ ref.label ?? ref.source + '#' + ref.ref }}</a>
            } @else {
              <span class="iss" data-testid="detail-pointer">{{ ref.label ?? ref.source + '#' + ref.ref }}</span>
            }
          }
          <span class="st" [attr.data-tone]="statusTone()" data-testid="machine-detail-status">
            {{ statusLabel() }} · node {{ nodeName() }} · a{{ epoch() }}
          </span>
        </span>
      </div>
      <div class="d-actions">
        @if (pause()) {
          <fleet-kit-button testid="resume-chunk" [ariaLabel]="'Resume chunk ' + chunkId()" (click)="onResume()">
            Resume
          </fleet-kit-button>
        } @else if (pausable()) {
          <fleet-kit-button testid="pause-chunk" [ariaLabel]="'Pause chunk ' + chunkId()" (click)="onPause()">
            Pause
          </fleet-kit-button>
        }
        <button type="button" class="close" data-testid="detail-close" aria-label="Close" (click)="dismiss.emit()">
          ✕
        </button>
      </div>
    </header>
  `,
  styles: `
    :host {
      display: contents;
    }
    /* Two clusters, space-between — the hub board's own header shape: identity
       on the left, actions on the right. Wraps rather than overflows once the
       dock is only as wide as a phone. Paints no chrome of its own (issue
       #307) — projected into KitPanel's own [header] slot in owns-the-bar
       mode (chunk-detail.ts's [hasHeaderContent], kit-panel.ts's own declared
       contract for that mode), which already sizes this root to the bar's
       full width and supplies the bar's background and border; a second copy
       here would stack two header bars. */
    .d-hdr {
      min-width: 0;
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 10px;
      font-family: var(--mono);
    }
    .d-title {
      display: flex;
      flex-direction: column;
      gap: 2px;
      min-width: 0;
    }
    .cid {
      display: block;
      color: var(--amber-hi);
      font-size: var(--fs-md);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      text-decoration: none;
    }
    .cid:hover,
    .cid:focus-visible {
      text-decoration: underline;
      outline: none;
    }
    .d-sub {
      display: flex;
      align-items: baseline;
      flex-wrap: wrap;
      gap: 8px;
      min-width: 0;
    }
    .d-sub .iss {
      color: var(--cyan);
      font-size: var(--fs-sm);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    a.iss {
      text-decoration: none;
    }
    a.iss:hover,
    a.iss:focus-visible {
      text-decoration: underline;
      outline: none;
    }
    .st {
      font-size: var(--fs-label);
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--label);
    }
    .st[data-tone='running'] {
      color: var(--amber);
    }
    .st[data-tone='stale'],
    .st[data-tone='needs'] {
      color: var(--red);
    }
    .st[data-tone='waiting'],
    .st[data-tone='takeover'] {
      color: var(--amber-hi);
    }
    .st[data-tone='spawning'] {
      color: var(--cyan);
    }
    .st[data-tone='done'] {
      color: var(--green);
    }
    .d-actions {
      flex: none;
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .close {
      background: transparent;
      border: 1px solid var(--bezel);
      color: var(--label-dim);
      cursor: pointer;
      font-family: inherit;
      padding: 2px 6px;
    }
    .close:hover {
      color: var(--text);
    }
  `,
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
   * `ChunkDetailView.pause`, never the machine-derived status. */
  readonly pause = input<runnerApi.PauseView | null>(null);

  /** Whether an **unpaused** chunk may be paused — container-folded off the
   * fresh `ChunkDetailView.status` (mirrors the hub `PauseService`'s refusal). */
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
