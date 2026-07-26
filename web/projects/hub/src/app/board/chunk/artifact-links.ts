import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { type ArtifactView } from 'fleet';

/**
 * The mobile chunk page's artifacts region — one **link per artifact** rather
 * than the desktop dock's inline bodies (`fleet`'s {@link ChunkArtifacts}).
 *
 * A findings asset's content is a wall of text: three of them inline would bury
 * the node history and asks above them in a single scrolling column, which is
 * exactly what the stacked shell exists to avoid. So this region renders the
 * index — key, kind, recency — and each row navigates one level deeper to
 * {@link ArtifactPage}, which shows that one artifact and nothing else.
 * Presentational: it renders the list it is handed and reads no query.
 */
@Component({
  selector: 'app-artifact-links',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink],
  template: `
    @if (artifacts().length === 0) {
      <p class="none" data-testid="mobile-artifacts-empty">No artifacts yet.</p>
    } @else {
      <ul class="list" data-testid="mobile-artifacts">
        @for (art of artifacts(); track art.key) {
          <li>
            <a
              class="art"
              data-testid="mobile-artifact-link"
              [attr.data-artifact-key]="art.key"
              [routerLink]="['/board', 'chunk', chunkId(), 'artifact', art.key]"
            >
              <span class="a-key">{{ art.key }}</span>
              <span class="a-kind">{{ art.kind }}</span>
              <span class="chev" aria-hidden="true">›</span>
            </a>
          </li>
        }
      </ul>
    }
  `,
  styles: `
    :host {
      display: block;
    }
    .none {
      margin: 0;
      padding: 6px 8px;
      color: var(--label-dim);
      font-size: var(--fs-xs);
    }
    .list {
      list-style: none;
      margin: 0;
      padding: 0;
    }
    /* A 44px row so the whole strip is the touch target, with the chevron
       carrying the "there is more behind this" affordance. */
    .art {
      display: flex;
      align-items: center;
      gap: 8px;
      min-height: 44px;
      padding: 0 10px;
      border-bottom: 1px solid var(--line);
      color: var(--cyan);
      font-size: var(--fs-xs);
      text-decoration: none;
    }
    .art:active {
      background: var(--bezel-hi);
    }
    .a-key {
      overflow-wrap: anywhere;
    }
    .a-kind {
      margin-left: auto;
      flex: none;
      color: var(--label-dim);
    }
    .chev {
      flex: none;
      color: var(--label);
      font-size: var(--fs-md);
      line-height: 1;
    }
  `,
})
export class ArtifactLinks {
  /** The chunk these artifacts belong to — the deeper route's own segment. */
  readonly chunkId = input.required<string>();

  /** The chunk's artifact store, server-ordered. */
  readonly artifacts = input<readonly ArtifactView[]>([]);
}
