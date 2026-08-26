import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';

import type { CreateWorkItemProposal, DocketEntryView, UpdateWorkItemProposal } from '../api/hub';

function isCreate(payload: CreateWorkItemProposal | UpdateWorkItemProposal): payload is CreateWorkItemProposal {
  return payload.kind !== 'update';
}

/**
 * The chunk's gate docket — every one of a chunk's not-yet-materialized proposals,
 * each with a per-entry strike toggle. A malformed stored proposal renders bare (kind
 * and proposing node only) rather than hiding or failing the gate. An entry already
 * struck (from a prior resolve, or another open gate on the same chunk) renders struck
 * with no toggle of its own — there is nothing left for this session to strike.
 *
 * Presentational only, and it owns the not-yet-submitted toggle set as local UI state
 * (`bzh:frontend-container-presentational`): every toggle emits {@link struckChange} so
 * the container can carry it into `resolveDecision` without reaching back into this
 * component's state.
 */
@Component({
  selector: 'fleet-chunk-detail-gate-docket',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './chunk-gate-docket.html',
  styleUrl: './chunk-gate-docket.css',
})
export class ChunkGateDocket {
  /** The chunk's pending proposals, as they stand at this gate. */
  readonly entries = input.required<readonly DocketEntryView[]>();

  /** Whether the current identity may resolve the gate (`gate:resolve`). Withholds the
   * strike toggles when `false`; the entries themselves still show. */
  readonly canResolve = input(false);

  /** Emitted with the full toggled-id set every time a toggle changes. */
  readonly struckChange = output<readonly string[]>();

  private readonly struck = signal<ReadonlySet<string>>(new Set());

  /** The currently toggled proposal ids. */
  readonly struckIds = computed<readonly string[]>(() => [...this.struck()]);

  /** Whether the entry renders struck — already struck server-side, or toggled this
   * session. */
  protected isStruck(entry: DocketEntryView): boolean {
    return entry.struck === true || this.struck().has(entry.proposal_id);
  }

  protected toggle(entry: DocketEntryView): void {
    if (entry.struck) return; // already struck server-side — nothing left to toggle
    const next = new Set(this.struck());
    if (next.has(entry.proposal_id)) next.delete(entry.proposal_id);
    else next.add(entry.proposal_id);
    this.struck.set(next);
    this.struckChange.emit([...next]);
  }

  /** The entry's display title — its create title, its update target, or a placeholder
   * when {@link DocketEntryView.malformed} leaves no field but kind/node/id reliable. */
  protected title(entry: DocketEntryView): string {
    const payload = entry.payload;
    if (entry.malformed || !payload) return '(unreadable proposal)';
    return isCreate(payload) ? payload.title : `${payload.source}#${payload.ref}`;
  }

  /** The entry's body preview — a create's markdown body, or an update's evidence. */
  protected detail(entry: DocketEntryView): string | null {
    const payload = entry.payload;
    if (entry.malformed || !payload) return null;
    return isCreate(payload) ? payload.body : payload.evidence;
  }
}
