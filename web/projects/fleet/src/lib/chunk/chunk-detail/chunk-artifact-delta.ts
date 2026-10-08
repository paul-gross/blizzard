import { ChangeDetectionStrategy, Component, TemplateRef, computed, input } from '@angular/core';

import type { AddFindingOp, FindingDelta, GoneFindingOp, ObservedFindingOp } from '../../api/hub';
import { compactRef } from '../../core/compact-ref';
import { KitFactList, type KitFact } from '../../kit/kit-fact-list';
import { KitProseBlock } from '../../kit/kit-prose-block';
import { ChunkArtifactRawDisclosure } from './chunk-artifact-raw-disclosure';
import { ChunkFindingEntry } from './chunk-finding-entry';
import { shortSha } from './short-sha';

/**
 * A parsed `FindingDelta` asset artifact, laid out. Presentational only
 * (`bzh:frontend-container-presentational`).
 *
 * Ops render in three groups, each hidden when empty. An `observed` or `gone`
 * entry is its bare id, since the raw ops carry no finding row to show; an `add`
 * entry is its candidate in full. The raw JSON stays one click away.
 */
@Component({
  selector: 'fleet-chunk-artifact-delta',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkArtifactRawDisclosure, ChunkFindingEntry, KitFactList, KitProseBlock],
  templateUrl: './chunk-artifact-delta.html',
  styleUrl: './chunk-artifact-delta.css',
})
export class ChunkArtifactDelta {
  /** The already-parsed delta to render. */
  readonly delta = input.required<FindingDelta>();

  /** The artifact's own verbatim content, forwarded to the raw disclosure. */
  readonly raw = input.required<string>();

  /** The root every handle this component renders derives from, so two mounts
   * never collide on one `data-testid`. */
  readonly testid = input('artifact');

  protected readonly compactRef = compactRef;
  protected readonly shortSha = shortSha;

  protected readonly added = computed(() => this.delta().findings.filter((f): f is AddFindingOp => f.op === 'add'));
  protected readonly observed = computed(() =>
    this.delta().findings.filter((f): f is ObservedFindingOp => f.op === 'observed'),
  );
  protected readonly gone = computed(() => this.delta().findings.filter((f): f is GoneFindingOp => f.op === 'gone'));

  protected readonly revisionEntries = computed(() => Object.entries(this.delta().revisions));

  /** The scope/revisions fact grid (`fleet-kit-fact-list`) — a method, not a stored
   * computed, since building the Revisions row needs the `<ng-template>` the view
   * declares for it. Revisions is
   * omitted entirely rather than rendered as an empty row when the delta names
   * none, matching the added/observed/gone groups' own hidden-when-empty rule. */
  protected factRows(delta: FindingDelta, revisionsValue: TemplateRef<unknown>): readonly KitFact[] {
    const rows: KitFact[] = [{ label: 'Scope', value: delta.scope, testid: `${this.testid()}-delta-scope` }];
    if (this.revisionEntries().length) {
      rows.push({ label: 'Revisions', template: revisionsValue, testid: `${this.testid()}-delta-revisions` });
    }
    return rows;
  }
}
