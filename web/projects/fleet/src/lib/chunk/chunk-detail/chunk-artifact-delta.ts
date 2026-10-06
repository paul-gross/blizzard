import { ChangeDetectionStrategy, Component, TemplateRef, computed, input } from '@angular/core';

import type { AddFindingOp, FindingDelta, GoneFindingOp, ObservedFindingOp } from '../../api/hub';
import { compactRef } from '../../core/compact-ref';
import { KitFactList, type KitFact } from '../../kit/kit-fact-list';
import { KitProseBlock } from '../../kit/kit-prose-block';
import { ChunkArtifactRawDisclosure } from './chunk-artifact-raw-disclosure';
import { ChunkFindingEntry } from './chunk-finding-entry';
import { shortSha } from './short-sha';

/**
 * A `FindingDelta`-shaped asset artifact, laid out — the structured rendering of a
 * content {@link parseFindingDelta} matched. Presentational
 * only: `delta` is already-parsed, plain data (`bzh:frontend-container-presentational`
 * — no query, no parsing here).
 *
 * Grouped by op into three distinct groups (shape shared with `run-delta.ts`), a
 * group with no entries hidden rather than rendered empty. An artifact's raw ops
 * carry no live finding row to join against, so an `observed`/`gone` entry here is its bare id, never a
 * class/locus/summary read back from a table this component has no query for.
 * `add`'s payload is the candidate itself, so it renders in full.
 *
 * The three ops' own semantics shape what each group shows: `observed` carries no payload beyond its id — "it was true
 * when recorded and is true now" — so its entry is the id alone; `gone` ordinarily does
 * not close the finding, it flags it for a person (except against a `delivered`
 * finding, which it settles outright — invisible to this raw-ops
 * render either way), which is why it renders beside `added` rather than looking like a
 * resolution.
 *
 * An `add` op's own body is {@link ChunkFindingEntry}. The raw JSON `content` stays
 * one click away inside {@link ChunkArtifactRawDisclosure}.
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
