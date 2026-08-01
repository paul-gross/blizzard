import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { RouterLink } from '@angular/router';

import type { ArtifactView, ChunkDetail } from '../api/hub';
import { ChunkArtifactBody } from './chunk-artifact-body';
import { sortArtifacts } from './sort-artifacts';

/**
 * The chunk's artifact store (issue #79, relinked #160) — every entry keyed
 * `{node}.{artifact-name}.{epoch}`, listed as **links**. Owns the ordering and
 * the empty state; each row's own rendering (the head, and for a `git_commit`
 * its pinned reference) belongs to {@link ChunkArtifactBody} in `summary` mode
 * — the same single owner the chunk detail page's Artifacts tab and the hub's
 * mobile artifact page compose too. Presentational only: the row is a plain
 * `routerLink`, not an injected `Router`.
 *
 * A row navigates to the chunk detail page's Artifacts tab with that artifact
 * pre-selected — an `asset`'s content can run hundreds of lines, which used to
 * bury every other artifact and every dock section below it inline. `linkBase`
 * defaults to the desktop board's own route (`/board/chunk`) so this `fleet`
 * library component needs no forwarding through {@link ChunkDetailPanel} /
 * {@link ChunkDetail} to reach it.
 */
@Component({
  selector: 'fleet-chunk-detail-artifacts',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkArtifactBody, RouterLink],
  template: `
    <div class="arts">
      <div class="s-head"><span class="tag" id="chunk-artifacts-heading">Artifacts</span></div>
      @if (artifacts().length === 0) {
        <p class="none" data-testid="artifacts-empty">No artifacts yet.</p>
      } @else {
        <ul class="artifacts" data-testid="artifacts">
          @for (art of artifacts(); track art.key) {
            <li class="artifact" data-testid="artifact" [attr.data-kind]="art.kind">
              <a
                class="artifact-link"
                data-testid="artifact-link"
                [attr.data-artifact-key]="art.key"
                [routerLink]="[...linkBase(), chunkId()]"
                [queryParams]="{ tab: 'artifacts', artifact: art.key }"
              >
                <fleet-chunk-detail-artifact-body [artifact]="art" body="summary" />
              </a>
            </li>
          }
        </ul>
      }
    </div>
  `,
  styles: `
    :host {
      display: block;
    }
    .tag {
      font-size: var(--fs-label);
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--label);
    }
    .arts {
      margin-bottom: 8px;
    }
    .s-head {
      margin-bottom: 6px;
    }
    .none {
      color: var(--label-dim);
      font-size: var(--fs-xs);
    }
    .artifacts {
      list-style: none;
      margin: 0;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .artifact {
      border: 1px solid var(--line);
      background: var(--overlay-20);
      padding: 4px 5px;
    }
    .artifact-link {
      display: block;
      color: inherit;
      text-decoration: none;
    }
    .artifact-link:hover,
    .artifact-link:focus-visible {
      color: var(--cyan);
      outline: none;
    }
  `,
})
export class ChunkArtifacts {
  /** The chunk aggregate to render (its attached artifacts). */
  readonly detail = input.required<ChunkDetail>();

  /** The chunk detail route's own path segments, before the chunk id — lets a
   * consumer outside the desktop board point this link elsewhere without
   * `fleet` hardcoding a hub route. */
  readonly linkBase = input<readonly string[]>(['/board', 'chunk']);

  protected readonly chunkId = computed(() => this.detail().chunk_id);

  /** The artifact store, oldest attachment first — see {@link sortArtifacts}. */
  protected readonly artifacts = computed<readonly ArtifactView[]>(() => sortArtifacts(this.detail().artifacts ?? []));
}
