import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { ArtifactKind, type ArtifactView, type FindingDelta, type FindingSurvey } from '../../api/hub';
import { formatAbsolute, formatWhen } from '../../core/when';
import { ChunkArtifactBounce } from './chunk-artifact-bounce';
import { ChunkArtifactDelta } from './chunk-artifact-delta';
import { ChunkArtifactSurvey } from './chunk-artifact-survey';
import { type BounceEnvelope, parseBounceEnvelope } from './parse-bounce-envelope';
import { parseFindingDelta } from './parse-finding-delta';
import { parseFindingSurvey } from './parse-finding-survey';

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
 * neither renders verbatim.
 *
 * The single owner of that rendering: every surface that shows an artifact
 * composes this rather than re-typing the kind branch, so a new field or a third
 * `kind` lands once (`canon:one-owner`).
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
 * to `artifact`. Both structured bodies receive the same root and
 * append their own `-delta`/`-survey` suffix.
 *
 * Laid out as a flex column with the asset body taking the free space: in an
 * auto-height host that resolves to the content's own height, and in a height-capped page it gives the body the scroll region
 * a long findings text — verbatim or structured — needs.
 */
@Component({
  selector: 'fleet-chunk-detail-artifact-body',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkArtifactBounce, ChunkArtifactDelta, ChunkArtifactSurvey],
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

  /** The root every handle this component renders derives from. Defaults to
   * `artifact`. */
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

  /** {@link when}'s full local date + time, for the stamp's hover tooltip. */
  protected readonly whenTitle = computed(() => formatAbsolute(this.artifact().recorded_at));

  protected readonly isAsset = computed(() => this.artifact().kind === ArtifactKind.ASSET);

  /** The asset's content, parsed as a `FindingDelta` — `null` when there is no
   * content to try ({@link structuredCandidate}) or it fails
   * {@link parseFindingDelta}'s shape check, in which case the template falls through
   * to the survey branch and then to the verbatim `<pre>`. */
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

  /** The `bounce-envelope` asset's content, parsed — `null` for any other artifact or an
   * envelope that is not a JSON object, which falls through to the verbatim `<pre>`.
   * Unlike the garden shapes this one is keyed by name: an envelope's `cause`/`detail`
   * carry no shape that tells it from any other small JSON asset. */
  protected readonly parsedBounce = computed<BounceEnvelope | null>(() => {
    const content = this.structuredCandidate();
    return content === null || this.artifact().key !== 'bounce-envelope' ? null : parseBounceEnvelope(content);
  });

  /** The asset content a structured reading may be attempted on, or `null` when there
   * is none to attempt: anything that isn't an asset, isn't rendering in `full`, or
   * carries no content. Gated on `body() === 'full'` so `summary` mounts
   * never spend a parse on content they don't render anyway. */
  private readonly structuredCandidate = computed<string | null>(() => {
    const artifact = this.artifact();
    if (artifact.kind !== ArtifactKind.ASSET || this.body() !== 'full') return null;
    return artifact.content ?? null;
  });
}
