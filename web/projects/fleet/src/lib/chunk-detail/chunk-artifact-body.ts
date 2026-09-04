import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import type { ArtifactView } from '../api/hub';
import { formatAbsolute, formatWhen } from '../when';
import { ChunkArtifactDelta } from './chunk-artifact-delta';
import { ChunkArtifactSurvey } from './chunk-artifact-survey';
import { parseFindingDelta, type FindingDelta } from './parse-finding-delta';
import { parseFindingSurvey, type FindingSurvey } from './parse-finding-survey';

/**
 * One artifact, rendered — the head (key, recency, kind) over a kind-dependent
 * body: an **asset**'s content, a **git_commit**'s pinned `repo @ commit` with its
 * branch link. An asset's content renders verbatim unless it parses as JSON matching
 * one of the two garden shapes a routine publishes — a `FindingDelta`
 * (`parse-finding-delta.ts`) through {@link ChunkArtifactDelta}, or a survey
 * (`parse-finding-survey.ts`) through {@link ChunkArtifactSurvey} — with the verbatim
 * text still one click away behind the raw-JSON toggle either way.
 *
 * Detection is by shape, never by the artifact's own name, and the two shapes are
 * disjoint by exactly one key: a survey and the delta published beside it share
 * `scope`/`revisions`/`measurement`, and differ only in the delta's op-tagged
 * `findings` versus the survey's identity-less `candidates`. Delta is tried first, so
 * a document somehow carrying both reads as the delivered shape. Anything matching
 * neither renders verbatim exactly as before.
 *
 * The single owner of that rendering. Three shells show an artifact —
 * the desktop dock's link row (`summary`), the chunk detail page's Artifacts tab
 * viewer (`full`), and the hub's mobile shell's single-artifact page (`full`) —
 * and all three compose this rather than re-typing the kind branch, so a new
 * field or a third `kind` lands once (`canon:one-owner`).
 *
 * `body` chooses how much of an asset renders: `full` (the default) is the
 * content, verbatim or structured; `summary` renders only the head — and, for a
 * `git_commit`, the ref line too, since that line is already a one-liner with
 * nothing to summarize away. A row that only ever links elsewhere (the dock) has
 * no use for a findings transcript's hundreds of lines, so it never attempts the
 * shape check either; a page whose whole job is showing one artifact wants the
 * content.
 *
 * `testid` roots every handle this component renders, the same convention
 * {@link MobileTitlebar} uses, so two mounts never collide on one
 * `data-testid` (`bzh:frontend-kit`'s globally-unique handle rule). It defaults
 * to `artifact` — the desktop dock's existing handles — so that side needs no
 * input to keep its specs passing. Both structured bodies receive the same root and
 * append their own `-delta`/`-survey` suffix.
 *
 * Laid out as a flex column with the asset body taking the free space: in the
 * dock's auto-height list item that resolves to the content's own height
 * (unchanged), and in a height-capped page it gives the body the scroll region
 * a long findings text — verbatim or structured — needs.
 */
@Component({
  selector: 'fleet-chunk-detail-artifact-body',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkArtifactDelta, ChunkArtifactSurvey],
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

  /** The asset's content, parsed as a `FindingDelta` — `null` when there is no
   * content to try ({@link structuredCandidate}) or it fails
   * {@link parseFindingDelta}'s shape check, in which case the template falls through
   * to the survey branch and then to the verbatim `<pre>`, unchanged. */
  protected readonly parsedDelta = computed<FindingDelta | null>(() => {
    const content = this.structuredCandidate();
    return content === null ? null : parseFindingDelta(content);
  });

  /** The asset's content, parsed as a survey — `null` under the same conditions
   * {@link parsedDelta} returns `null` for, plus one more: a content that already
   * parsed as a delta is never re-read as a survey, so the template's fallback chain
   * has a single winner even if a document ever carried both keys. */
  protected readonly parsedSurvey = computed<FindingSurvey | null>(() => {
    if (this.parsedDelta() !== null) return null;
    const content = this.structuredCandidate();
    return content === null ? null : parseFindingSurvey(content);
  });

  /** The asset content a structured reading may be attempted on, or `null` when there
   * is none to attempt: anything that isn't an asset, isn't rendering in `full`, or
   * carries no content. Gated on `body() === 'full'` so the dock's `summary` mounts
   * never spend a parse on content they don't render anyway. */
  private readonly structuredCandidate = computed<string | null>(() => {
    const artifact = this.artifact();
    if (artifact.kind !== 'asset' || this.body() !== 'full') return null;
    return artifact.content ?? null;
  });
}
