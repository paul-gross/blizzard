import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import type { ArtifactView, ChunkDetail } from '../api/hub';
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
 * Two row modes, since not every host has somewhere to send a click: `link`
 * (default) — a row is a plain `routerLink` (no injected `Router`) that
 * navigates to the chunk detail page's Artifacts tab with that artifact
 * pre-selected, rendering {@link ChunkArtifactBody} in `summary` mode. An
 * `asset`'s content can run hundreds of lines, which used to bury every
 * other artifact and every dock section below it inline. `linkBase` defaults
 * to the desktop board's own route (`/board/chunk`) so this `fleet` library
 * component needs no forwarding through {@link ChunkDetailPanel} /
 * {@link ChunkDetail} to reach it. The link carries only its own
 * `tab`/`artifact` pair: the chunk rides the destination route's own path,
 * and the one host still on this mode navigates from `/board`, whose
 * `?chunk=` selection means nothing on the page it opens.
 *
 * `expandable` — for a host with no Artifacts tab of its own (the runner's
 * single-column chunk detail route, issue #318, which has nowhere for
 * `linkBase` to point): a row with a body worth hiding is a `<button>` that
 * toggles its own {@link ChunkArtifactBody} between `summary` and `full` in
 * place, an accordion rather than a navigation, at most one row expanded at a
 * time. A row with nothing to reveal renders as plain content and no toggle
 * at all — see {@link hasBodyToExpand}.
 */
@Component({
  selector: 'fleet-chunk-detail-artifacts',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkArtifactBody, RouterLink],
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

  /** `true` renders each row as an in-place expand/collapse toggle instead of
   * a `routerLink` — for a host with no Artifacts tab of its own to link to.
   * `false` (the default) is every existing consumer's current behavior. */
  readonly expandable = input(false);

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

  /** The one row currently expanded in {@link expandable} mode, or `null`. */
  protected readonly expandedKey = signal<string | null>(null);

  /** Whether an {@link expandable}-mode row has anything an expand would reveal:
   * only an `asset` with content does. {@link ChunkArtifactBody} renders a
   * `git_commit` identically in `summary` and `full` — its ref line is a one-liner
   * with nothing to summarize away — so a toggle over one announces an expansion
   * that changes nothing on screen, and wraps that line's real branch `<a>` in a
   * `<button>`: an interactive anchor inside an interactive control, which is
   * invalid content model and gives a click two conflicting targets. Such a row
   * renders as plain content instead, with no toggle at all. */
  protected hasBodyToExpand(artifact: ArtifactView): boolean {
    return artifact.kind === 'asset' && !!artifact.content;
  }

  protected toggle(key: string): void {
    this.expandedKey.update((current) => (current === key ? null : key));
  }
}
