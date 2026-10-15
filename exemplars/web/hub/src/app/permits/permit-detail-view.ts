// exemplar: bzh:frontend-container-presentational, bzh:frontend-kit-floor — a fact grid from the kit.

import { ChangeDetectionStrategy, Component, type TemplateRef, computed, input, viewChild } from '@angular/core';
import { RouterLink } from '@angular/router';

import { type KitAsyncStateValue, type KitFact, type PermitView, FleetWhen, KitAsyncState, KitFactList, KitPanel } from 'fleet';
import { LensLink } from 'fleet/shell';
import { permitProjectLabel } from './permits-page.model';

/** A panel of one permit's facts, linking back to the listing. */
@Component({
  selector: 'app-permit-detail-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetWhen, KitAsyncState, KitFactList, KitPanel, LensLink, RouterLink],
  templateUrl: './permit-detail-view.html',
})
export class PermitDetailView {
  /** The permit to show; `null` until there is one. */
  readonly permit = input<PermitView | null>(null);
  /** Whether the permit is resting, loading, failed or ready. */
  readonly state = input.required<KitAsyncStateValue>();

  private readonly requestedAt = viewChild.required<TemplateRef<unknown>>('requestedAt');

  protected readonly facts = computed<readonly KitFact[]>(() => {
    const permit = this.permit();
    if (permit === null) return [];
    return [
      { label: 'Project', value: permitProjectLabel(permit) },
      { label: 'Holder', value: permit.holder },
      { label: 'Capability', value: permit.capability },
      { label: 'State', value: permit.state, testid: 'permit-detail-state' },
      { label: 'Requested', template: this.requestedAt() },
      { label: 'Requested by', value: permit.requested_by },
      ...(permit.changed_by ? [{ label: 'Changed by', value: permit.changed_by }] : []),
      ...(permit.revocation_note ? [{ label: 'Revocation note', value: permit.revocation_note }] : []),
    ];
  });
}
