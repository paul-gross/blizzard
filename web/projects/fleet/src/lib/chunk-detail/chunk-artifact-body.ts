import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import type { ArtifactView } from '../api/hub';
import { formatAbsolute, formatWhen } from '../when';

/**
 * One artifact, rendered — the head (key, recency, kind) over a kind-dependent
 * body: an **asset**'s content verbatim, a **git_commit**'s pinned `repo @ commit`
 * with its branch link.
 *
 * The single owner of that rendering. Three shells show an artifact —
 * the desktop dock's link row (`summary`), the chunk detail page's Artifacts tab
 * viewer (`full`), and the hub's mobile shell's single-artifact page (`full`) —
 * and all three compose this rather than re-typing the kind branch, so a new
 * field or a third `kind` lands once (`canon:one-owner`).
 *
 * `body` chooses how much of an asset renders: `full` (the default) is the
 * content verbatim; `summary` renders only the head — and, for a `git_commit`,
 * the ref line too, since that line is already a one-liner with nothing to
 * summarize away. A row that only ever links elsewhere (the dock) has no use
 * for a findings transcript's hundreds of lines; a page whose whole job is
 * showing one artifact wants the content.
 *
 * `testid` roots every handle this component renders, the same convention
 * {@link MobileTitlebar} uses, so two mounts never collide on one
 * `data-testid` (`bzh:frontend-kit`'s globally-unique handle rule). It defaults
 * to `artifact` — the desktop dock's existing handles — so that side needs no
 * input to keep its specs passing.
 *
 * Laid out as a flex column with the asset body taking the free space: in the
 * dock's auto-height list item that resolves to the content's own height
 * (unchanged), and in a height-capped page it gives the body the scroll region
 * a long findings text needs.
 */
@Component({
  selector: 'fleet-chunk-detail-artifact-body',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './chunk-artifact-body.html',
  styleUrl: './chunk-artifact-body.css',
})
export class ChunkArtifactBody {
  /** The artifact to render. */
  readonly artifact = input.required<ArtifactView>();

  /** `full` (default) renders an asset's content verbatim; `summary` omits it —
   * a `git_commit`'s ref line renders either way, since it carries nothing to
   * summarize away. */
  readonly body = input<'full' | 'summary'>('full');

  /** The root every handle this component renders derives from. Defaults to the
   * desktop dock's existing `artifact-*` handles. */
  readonly testid = input('artifact');

  protected readonly keyTestid = computed(() => `${this.testid()}-key`);
  protected readonly whenTestid = computed(() => `${this.testid()}-when`);
  protected readonly contentTestid = computed(() => `${this.testid()}-content`);
  protected readonly refTestid = computed(() => `${this.testid()}-ref`);
  protected readonly branchTestid = computed(() => `${this.testid()}-branch`);

  /** The attachment instant, pre-formatted, or `null` when the entry carries none. */
  protected readonly when = computed(() => {
    const at = this.artifact().recorded_at;
    return at ? formatWhen(at) : null;
  });

  /** {@link when}'s full local date + time, for the stamp's hover tooltip (issue #175) —
   * replaces the raw-ISO `title` this span carried before, which didn't localize. */
  protected readonly whenTitle = computed(() => formatAbsolute(this.artifact().recorded_at));
}
