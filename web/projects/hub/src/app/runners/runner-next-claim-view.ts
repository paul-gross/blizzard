import { NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { compactRef, type hubApi, KitAccordionSection, KitAsyncState, KitUrgencyPill, type KitAsyncStateValue, type UrgencyView } from 'fleet';
import { LensLink } from 'fleet/shell';
import { nextClaimSummary } from './runner-next-claim.model';
@Component({
  selector: 'app-runner-next-claim-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [NgTemplateOutlet, KitAccordionSection, KitAsyncState, KitUrgencyPill, RouterLink, LensLink],
  templateUrl: './runner-next-claim-view.html',
  styleUrl: './runner-next-claim-view.css',
})
export class RunnerNextClaimView {
  readonly runnerId = input.required<string>();
  readonly data = input<hubApi.NextClaimResponse | null>(null);
  readonly state = input.required<KitAsyncStateValue>();
  readonly urgencies = input<readonly UrgencyView[]>([]);
  readonly urgencyIconUrl = input<string | null>(null);
  readonly statuses = input<Readonly<Record<string, string | undefined>>>({});
  protected readonly expanded = signal(false);
  protected readonly compact = compactRef;
  protected readonly summary = nextClaimSummary;
  protected urgency(ref: hubApi.UrgencyRef | null): UrgencyView | null {
    return this.urgencies().find((item) => item.urgency_id === ref?.urgency_id) ?? null;
  }
}
