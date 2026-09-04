import { ChangeDetectionStrategy, Component, TemplateRef, computed, input } from '@angular/core';

import { KitFactList, type KitFact } from '../kit/kit-fact-list';
import { KitProseBlock } from '../kit/kit-prose-block';
import { ChunkArtifactRawDisclosure } from './chunk-artifact-raw-disclosure';
import { ChunkFindingEntry } from './chunk-finding-entry';
import type { FindingSurvey } from './parse-finding-survey';
import { shortSha } from './short-sha';

/**
 * A survey-shaped asset artifact, laid out — {@link parseFindingSurvey}'s match branch
 * of `chunk-artifact-body.ts`'s asset body, the sibling of {@link ChunkArtifactDelta}.
 * Presentational only: `survey` is already-parsed, plain data
 * (`bzh:frontend-container-presentational` — no query, no parsing here).
 *
 * Deliberately the delta's layout with one group instead of three: the same
 * scope/revisions fact grid, the same measurement prose, then the candidates. A garden
 * run publishes both artifacts from the same node with the same head
 * (`garden-routine/prompts/survey.md`), and they are read next to each other in one
 * Artifacts tab, so a reader should not have to re-learn the layout between them —
 * which is also why a candidate renders through the shared
 * {@link ChunkFindingEntry} rather than a second copy of the delta's `add` entry.
 *
 * There is no added/observed/gone split here, and that absence is the point: a survey
 * is what one session saw, not a delta against what stood before. Its entries carry no
 * `fin_` id at all — the hub mints identity at delivery — so there is nothing here to
 * group by, and nothing to link to a standing finding row.
 *
 * The candidate group renders even when empty, unlike the delta's hidden-when-empty
 * groups: a clean sweep is a real, meaningful survey result (`survey.md`'s "a clean
 * sweep still delivers"), and a page that simply omits the list leaves a reader unable
 * to tell "found nothing" from "this app failed to render the list".
 */
@Component({
  selector: 'fleet-chunk-artifact-survey',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkArtifactRawDisclosure, ChunkFindingEntry, KitFactList, KitProseBlock],
  templateUrl: './chunk-artifact-survey.html',
  styleUrl: './chunk-artifact-survey.css',
})
export class ChunkArtifactSurvey {
  /** The already-parsed survey to render. */
  readonly survey = input.required<FindingSurvey>();

  /** The artifact's own verbatim content, forwarded to the raw disclosure. */
  readonly raw = input.required<string>();

  /** The root every handle this component renders derives from — the same convention
   * {@link ChunkArtifactBody} itself follows, so two mounts (the Artifacts tab and the
   * mobile single-artifact page) never collide on one `data-testid`. */
  readonly testid = input('artifact');

  protected readonly shortSha = shortSha;

  protected readonly revisionEntries = computed(() => Object.entries(this.survey().revisions));

  /** The scope/revisions fact grid — a method, not a stored computed, since building
   * the Revisions row needs the `<ng-template>` the view declares for it, exactly as
   * {@link ChunkArtifactDelta.factRows} does. */
  protected factRows(survey: FindingSurvey, revisionsValue: TemplateRef<unknown>): readonly KitFact[] {
    const rows: KitFact[] = [{ label: 'Scope', value: survey.scope, testid: `${this.testid()}-survey-scope` }];
    if (this.revisionEntries().length) {
      rows.push({ label: 'Revisions', template: revisionsValue, testid: `${this.testid()}-survey-revisions` });
    }
    return rows;
  }
}
