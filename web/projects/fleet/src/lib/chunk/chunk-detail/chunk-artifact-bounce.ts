import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { KitFactList, type KitFact } from '../../kit/kit-fact-list';
import { ChunkArtifactRawDisclosure } from './chunk-artifact-raw-disclosure';
import type { ParsedBounceEnvelope } from './parse-bounce-envelope';

/**
 * A `bounce-envelope` asset, laid out as labelled fields — cause, detail, the action whose pass answered the
 * bounce, then each repository that did not land — with the verbatim JSON one click away behind the raw disclosure. Presentational only: `envelope` is already validated
 * ({@link parseBounceEnvelope}).
 */
@Component({
  selector: 'fleet-chunk-artifact-bounce',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkArtifactRawDisclosure, KitFactList],
  templateUrl: './chunk-artifact-bounce.html',
})
export class ChunkArtifactBounce {
  /** The already-validated envelope to render. */
  readonly envelope = input.required<ParsedBounceEnvelope>();

  /** The artifact's own verbatim content, forwarded to the raw disclosure. */
  readonly raw = input.required<string>();

  /** The root every handle this component renders derives from. */
  readonly testid = input('artifact');

  protected readonly rows = computed<readonly KitFact[]>(() => {
    const { cause, detail, diagnostic, action, unlanded } = this.envelope();
    const rows: KitFact[] = [
      { label: 'Cause', value: cause, testid: `${this.testid()}-bounce-cause` },
    ];
    // A written diagnostic is shown verbatim, a known-none says so, and a historical body (no key) shows its detail.
    if (diagnostic === undefined) rows.push({ label: 'Detail', value: detail, testid: `${this.testid()}-bounce-detail` });
    else rows.push({ label: 'Diagnostic', value: diagnostic ?? 'none', testid: `${this.testid()}-bounce-diagnostic` });
    if (action !== null) {
      rows.push({ label: 'Action', value: `${action.name} answered ${action.choice}`, testid: `${this.testid()}-bounce-action` });
    }
    unlanded.forEach((repo, index) => {
      const pr = repo.pr === null ? '' : `, PR #${repo.pr.number} ${repo.pr.url}`;
      rows.push({ label: 'Unlanded', value: `${repo.repo} @ ${repo.branch}${pr}`, testid: `${this.testid()}-bounce-unlanded-${index}` });
    });
    return rows;
  });
}
