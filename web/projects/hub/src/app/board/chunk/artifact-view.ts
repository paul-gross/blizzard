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
  templateUrl: './artifact-view.html',
  styleUrl: './artifact-view.css',
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
