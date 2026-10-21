import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import type { BounceEnvelope } from '../../api/hub';
import { KitFactList, type KitFact } from '../../kit/kit-fact-list';
import { ChunkArtifactRawDisclosure } from './chunk-artifact-raw-disclosure';

/**
 * A `bounce-envelope` asset, laid out as labelled fields — cause, detail, the failed step, then each
 * repository that did not land — with the verbatim JSON one click away behind the raw
 * disclosure. Presentational only: `envelope` is already validated
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
  readonly envelope = input.required<BounceEnvelope>();

  /** The artifact's own verbatim content, forwarded to the raw disclosure. */
  readonly raw = input.required<string>();

  /** The root every handle this component renders derives from. */
  readonly testid = input('artifact');

  protected readonly rows = computed<readonly KitFact[]>(() => {
    const { cause, detail, step, unlanded } = this.envelope();
    const rows: KitFact[] = [
      { label: 'Cause', value: cause, testid: `${this.testid()}-bounce-cause` },
      { label: 'Detail', value: detail, testid: `${this.testid()}-bounce-detail` },
    ];
    if (step !== null) {
      rows.push({ label: 'Step', value: `${step.name} exited ${step.exit_code}`, testid: `${this.testid()}-bounce-step` });
      if (step.output_tail !== '') rows.push({ label: 'Output', value: step.output_tail, testid: `${this.testid()}-bounce-output` });
    }
    unlanded.forEach((repo, index) => {
      const pr = repo.pr === null ? '' : `, PR #${repo.pr.number} ${repo.pr.url}`;
      rows.push({ label: 'Unlanded', value: `${repo.repo} @ ${repo.branch}${pr}`, testid: `${this.testid()}-bounce-unlanded-${index}` });
    });
    return rows;
  });
}
