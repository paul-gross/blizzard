import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';

import type { CreateWorkItemProposal, DocketEntryView, UpdateWorkItemProposal } from '../api/hub';

function isCreate(payload: CreateWorkItemProposal | UpdateWorkItemProposal): payload is CreateWorkItemProposal {
  return payload.kind !== 'update';
}

/**
 * The chunk's gate docket (blizzard#367) — every one of its not-yet-materialized
 * proposals, each with a per-entry strike toggle. A malformed stored proposal renders
 * bare (kind and proposing node only) rather than hiding or failing the gate.
 *
 * Presentational only, and it owns the toggle set as local UI state
 * (`bzh:frontend-container-presentational`): {@link ChunkAwaitingHuman} reads
 * {@link struckIds} through a view child at the moment it emits `resolveDecision`,
 * rather than the selection round-tripping back up through an output on every toggle.
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

  private readonly struck = signal<ReadonlySet<string>>(new Set());

  /** The currently toggled proposal ids — read by the parent at resolve time. */
  readonly struckIds = computed<readonly string[]>(() => [...this.struck()]);

  protected isStruck(proposalId: string): boolean {
    return this.struck().has(proposalId);
  }

  protected toggle(proposalId: string): void {
    const next = new Set(this.struck());
    if (next.has(proposalId)) next.delete(proposalId);
    else next.add(proposalId);
    this.struck.set(next);
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
