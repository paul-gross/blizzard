import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { KitProseBlock } from '../kit/kit-prose-block';
import { shortSha } from './short-sha';

/** The fields a finding reads by before it has an identity — what a survey's
 * `FindingCandidate` and a delta's `AddFindingOp` both carry
 * (`src/blizzard/wire/finding.py`: the op is documented as "the candidate minus its
 * identity", so the two agree field for field). Structural, not a re-declaration of
 * either: both of this directory's parsed shapes satisfy it as they stand. */
export interface FindingEntryView {
  readonly class: string;
  readonly locus: string;
  readonly summary: string;
  readonly introduced: string | null;
  readonly ref: string | null;
}

/**
 * One identity-less finding, laid out — the class/locus head, the best-effort
 * `introduced` revision, and the summary as agent prose. The single owner of that
 * layout ({@link canon:one-owner} in spirit): a garden run publishes the same entry
 * twice, once as a survey candidate and once as a delta's `add` op, and the two are
 * read side by side in the same Artifacts tab, so they must not drift apart in how
 * they render.
 *
 * Presentational only — `entry` is already-parsed plain data
 * (`bzh:frontend-container-presentational`). Renders no list item of its own: the
 * parent owns the `<ul>`/`<li>` so each surface keeps its own list semantics and
 * spacing.
 */
@Component({
  selector: 'fleet-chunk-finding-entry',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitProseBlock],
  templateUrl: './chunk-finding-entry.html',
  styleUrl: './chunk-finding-entry.css',
})
export class ChunkFindingEntry {
  readonly entry = input.required<FindingEntryView>();

  /** The summary prose block's own `data-testid` — supplied by the parent, since only
   * it knows which surface and which index this entry sits at. */
  readonly summaryTestid = input<string | null>(null);

  protected readonly shortSha = shortSha;
}
