import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { type ArtifactView, ChunkArtifactsPanel } from 'fleet';

/**
 * The chunk detail page's Artifacts tab — a thin host for
 * {@link ChunkArtifactsPanel}, the nav-list-beside-a-viewer shape this tab
 * shipped first and the runner's chunk detail page now shares.
 *
 * `testidPrefix="artifacts-tab"` keeps every handle this tab has always
 * rendered — `DemoDirector`'s unattended kiosk tour steers by
 * `artifacts-tab-artifact` / `artifacts-tab-artifact-key` outside any test
 * harness, so this tab cannot silently drift onto the shared component's own
 * `artifacts-panel-*` default without stalling the demo mid-tour.
 */
@Component({
  selector: 'app-chunk-artifacts-tab',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkArtifactsPanel],
  templateUrl: './chunk-artifacts-tab.html',
  styleUrl: './chunk-artifacts-tab.css',
})
export class ChunkArtifactsTab {
  /** The chunk's artifact store, server-ordered. */
  readonly artifacts = input.required<readonly ArtifactView[]>();

  /** The raw `?artifact` URL param — the requested selection, before
   * {@link ChunkArtifactsPanel} falls it back to the most recent entry. */
  readonly selectedKey = input<string | null>(null);

  /** Emitted with a nav row's key when the operator picks it. */
  readonly pickArtifact = output<string>();
}
