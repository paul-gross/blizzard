import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import {
  type ArtifactView as ArtifactModel,
  ChunkArtifactBody,
  KitAsyncState,
  type KitAsyncStateValue,
  KitBackBar,
} from 'fleet';

/**
 * The single-artifact screen, presentational — the back affordance over one
 * {@link ChunkArtifactBody}, the same renderer the desktop dock's own list uses
 * for each of its entries. This component owns no query: it renders the artifact
 * it is handed and whichever async state the container resolved, so its spec runs
 * on plain inputs rather than a stubbed transport
 * (`bzh:frontend-container-presentational`).
 */
@Component({
  selector: 'app-artifact-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkArtifactBody, KitAsyncState, KitBackBar, RouterLink],
  template: `
    <div class="ap">
      <a class="back-row" [routerLink]="['/board', 'chunk', chunkId()]" data-testid="mobile-artifact-back">
        <fleet-kit-back-bar [label]="backLabel()" />
      </a>
      <div class="ap-body">
        <fleet-kit-async-state
          [state]="state()"
          loadingText="LOADING…"
          loadingTestid="mobile-artifact-loading"
          errorText="CHUNK UNAVAILABLE"
          errorTestid="mobile-artifact-error"
          emptyText="NO SUCH ARTIFACT"
          emptyTestid="mobile-artifact-missing"
        >
          @if (artifact(); as art) {
            <fleet-chunk-detail-artifact-body
              class="body"
              [artifact]="art"
              testid="mobile-artifact"
              data-testid="mobile-artifact"
            />
          }
        </fleet-kit-async-state>
      </div>
    </div>
  `,
  styles: `
    :host {
      display: block;
      flex: 1;
      min-height: 0;
      font-family: var(--mono);
      font-size: var(--fs-base);
      font-variant-numeric: tabular-nums;
      color: var(--text);
    }
    .ap {
      display: flex;
      flex-direction: column;
      height: 100%;
      min-height: 0;
      overflow: hidden;
    }
    .back-row {
      flex: none;
      text-decoration: none;
    }
    /* Positioned and height-bearing so KitAsyncState's absolutely centered status
       line has a box to center in, and so the artifact body inherits the scroll
       region a long findings text needs — the reason this screen exists. */
    .ap-body {
      position: relative;
      display: flex;
      flex-direction: column;
      flex: 1;
      min-height: 0;
      padding: 8px;
    }
    .body {
      flex: 1;
      min-height: 0;
    }
  `,
})
export class ArtifactView {
  /** The chunk this artifact belongs to — the back link's target. */
  readonly chunkId = input<string | null>(null);

  /** The chunk's compact ref, for the back link's label. */
  readonly backLabel = input('chunk');

  /** The artifact to render, or `null` when there is nothing to show. */
  readonly artifact = input<ArtifactModel | null>(null);

  /** Which of the four async states to render — `empty` is a key that names
   * nothing in the store, which is a dead link rather than a fault. */
  readonly state = input.required<KitAsyncStateValue>();
}
