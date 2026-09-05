import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';
import { type ArtifactView as ArtifactModel, asyncState, compactRef, injectHubChunkDetailQuery } from 'fleet';

import { ArtifactView } from './artifact-view';

/**
 * One artifact, full (`/board/chunk/:chunkId/artifact/:artifactKey`) — out of
 * scope for the chunk detail page's Artifacts tab (issue #160), which shows
 * the same artifacts without leaving the page; this route stays live so an
 * already-shared URL keeps resolving. The route **container**
 * (`bzh:frontend-container-presentational`): it resolves the route params and the
 * read, and hands {@link ArtifactView} the artifact plus a settled async state.
 *
 * Reads nothing new: the artifact store rides on the chunk aggregate
 * {@link ChunkPage} already fetched, so this re-injects the *same*
 * `injectHubChunkDetailQuery` and picks its one entry out. Same query key, so
 * TanStack serves it from cache — arriving here is instant, and a cold deep link
 * (a shared URL, a reload) still resolves because the read is keyed by chunk id,
 * not by anything the previous page held in memory.
 */
@Component({
  selector: 'app-artifact-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ArtifactView],
  templateUrl: './artifact-page.html',
  styleUrl: './artifact-page.css',
})
export class ArtifactPage {
  private readonly route = inject(ActivatedRoute);

  private readonly params = toSignal(this.route.paramMap, { initialValue: this.route.snapshot.paramMap });

  protected readonly chunkId = computed<string | null>(() => this.params().get('chunkId'));
  private readonly artifactKey = computed<string | null>(() => this.params().get('artifactKey'));

  protected readonly shortId = computed(() => compactRef(this.chunkId() ?? ''));

  private readonly detailQuery = injectHubChunkDetailQuery(() => this.chunkId());

  /** The one artifact this page names, or `undefined` when the read has not
   * landed — or when the key names nothing in the store. {@link state} is what
   * tells those two apart. */
  protected readonly artifact = computed<ArtifactModel | undefined>(() => {
    const key = this.artifactKey();
    if (key === null) return undefined;
    return (this.detailQuery.data()?.artifacts ?? []).find((art) => art.key === key);
  });

  /** A resolved chunk whose store has no such key is a dead link (`empty`), not a
   * slow read (`loading`) and not a fault (`error`) — an operator following a
   * stale shared URL deserves to be told which. */
  protected readonly state = computed(() => asyncState(this.detailQuery, this.artifact() === undefined));
}
