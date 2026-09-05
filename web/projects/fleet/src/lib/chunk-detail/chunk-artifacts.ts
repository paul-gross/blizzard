import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { RouterLink } from '@angular/router';

import type { ArtifactView, ChunkDetail } from '../api/hub';
import { KitAsyncState } from '../kit/kit-async-state';
import { ChunkArtifactBody } from './chunk-artifact-body';
import { sortArtifacts } from './sort-artifacts';

/**
 * The chunk's artifact store (issue #79, relinked #160) — every entry keyed
 * `{node}.{artifact-name}.{epoch}`, listed as rows. Owns the ordering and the
 * empty state; each row's own rendering (the head, and for a `git_commit` its
 * pinned reference) belongs to {@link ChunkArtifactBody} — the same single
 * owner the chunk detail page's Artifacts tab and the hub's mobile artifact
 * page compose too.
 *
 * Every row is a plain `routerLink` (no injected `Router`) that navigates to
 * the chunk detail page's Artifacts tab with that artifact pre-selected,
 * rendering {@link ChunkArtifactBody} in `summary` mode. An `asset`'s content
 * can run hundreds of lines, which used to bury every other artifact and every
 * dock section below it inline. `linkBase` defaults to the desktop board's own
 * route (`/board/chunk`) so this `fleet` library component needs no forwarding
 * through {@link ChunkDetailPanel} / {@link ChunkDetail} to reach it. The link
 * carries only its own `tab`/`artifact` pair: the chunk rides the destination
 * route's own path, and the one host still on this mode navigates from
 * `/board`, whose `?chunk=` selection means nothing on the page it opens.
 */
@Component({
  selector: 'fleet-chunk-detail-artifacts',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkArtifactBody, KitAsyncState, RouterLink],
  templateUrl: './chunk-artifacts.html',
  styleUrl: './chunk-artifacts.css',
})
export class ChunkArtifacts {
  /** The chunk aggregate to render (its attached artifacts). */
  readonly detail = input.required<ChunkDetail>();

  /** The chunk detail route's own path segments, before the chunk id — lets a
   * consumer outside the desktop board point this link elsewhere without
   * `fleet` hardcoding a hub route. */
  readonly linkBase = input<readonly string[]>(['/board', 'chunk']);

  /** Whether to render this component's own "Artifacts" section heading. `true`
   * (the default) is today's behavior, kept for the desktop board dock
   * (`chunk-detail-panel.ts`'s `.d-sec`), which has no panel chrome of its own
   * around this component and relies on the heading both visually and as its
   * `aria-labelledby` target. A consumer that already wraps this component in
   * a titled `<fleet-kit-panel label="artifacts">` (issue #205) sets this
   * `false` so the label doesn't render twice. */
  readonly heading = input(true);

  protected readonly chunkId = computed(() => this.detail().chunk_id);

  /** The artifact store, oldest attachment first — see {@link sortArtifacts}. */
  protected readonly artifacts = computed<readonly ArtifactView[]>(() => sortArtifacts(this.detail().artifacts ?? []));
}
