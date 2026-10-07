import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { KitFactList, type KitFact } from '../../kit/kit-fact-list';
import { ChunkArtifactRawDisclosure } from './chunk-artifact-raw-disclosure';
import type { BounceEnvelope } from './parse-bounce-envelope';

/**
 * A `bounce-envelope` asset, laid out as labelled fields — cause, detail, then any
 * further keys the envelope carries — with the verbatim JSON one click away behind the
 * raw disclosure. Presentational only: `envelope` is already parsed
 * ({@link parseBounceEnvelope}).
 */
@Component({
  selector: 'fleet-chunk-artifact-bounce',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkArtifactRawDisclosure, KitFactList],
  templateUrl: './chunk-artifact-bounce.html',
})
export class ChunkArtifactBounce {
  /** The already-parsed envelope to render. */
  readonly envelope = input.required<BounceEnvelope>();

  /** The artifact's own verbatim content, forwarded to the raw disclosure. */
  readonly raw = input.required<string>();

  /** The root every handle this component renders derives from. */
  readonly testid = input('artifact');

  protected readonly rows = computed<readonly KitFact[]>(() => {
    const { cause, detail, extras } = this.envelope();
    const rows: KitFact[] = [];
    if (cause !== null) rows.push({ label: 'Cause', value: cause, testid: `${this.testid()}-bounce-cause` });
    if (detail !== null) rows.push({ label: 'Detail', value: detail, testid: `${this.testid()}-bounce-detail` });
    for (const [key, value] of extras) rows.push({ label: key, value });
    return rows;
  });
}
