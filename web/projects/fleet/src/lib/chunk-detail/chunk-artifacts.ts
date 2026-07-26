import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import type { ArtifactView, ChunkDetail } from '../api/hub';
import { ChunkArtifactBody } from './chunk-artifact-body';

/**
 * The chunk's artifact store (issue #79) — every entry keyed
 * `{node}.{artifact-name}.{epoch}`, stacked inline. Owns the ordering and the
 * empty state; each entry's own rendering (the head, an asset's content, a
 * git_commit's pinned reference) belongs to {@link ChunkArtifactBody}, which the
 * hub's mobile artifact page composes too. Presentational only.
 */
@Component({
  selector: 'fleet-chunk-detail-artifacts',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkArtifactBody],
  template: `
    <div class="arts">
      <div class="s-head"><span class="tag">Artifacts</span></div>
      @if (artifacts().length === 0) {
        <p class="none" data-testid="artifacts-empty">No artifacts yet.</p>
      } @else {
        <ul class="artifacts" data-testid="artifacts">
          @for (art of artifacts(); track art.key) {
            <li class="artifact" data-testid="artifact" [attr.data-kind]="art.kind">
              <fleet-chunk-detail-artifact-body [artifact]="art" />
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
  `,
})
export class ChunkArtifacts {
  /** The chunk aggregate to render (its attached artifacts). */
  readonly detail = input.required<ChunkDetail>();

  /** The artifact store, oldest attachment first — ordered by `recorded_at` (the
   * ULID-decoded attachment instant). Entries without a stamp keep the server's
   * store-key order among themselves. */
  protected readonly artifacts = computed<readonly ArtifactView[]>(() =>
    [...(this.detail().artifacts ?? [])].sort((a, b) => (a.recorded_at ?? '').localeCompare(b.recorded_at ?? '')),
  );
}
