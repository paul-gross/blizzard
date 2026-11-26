import { NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { compactRef, type Urgency, type UrgencyReference } from 'fleet/core';
import { KitAsyncState, KitSectionHead, type KitAsyncStateValue } from 'fleet/kit';
import { KitAccordionGroup, KitAccordionSection } from 'fleet/kit/accordion';
import { KitUrgencyPill } from 'fleet/kit/tooltip';
import { AppLink } from 'fleet/shell';
import { nextClaimSummary } from './runner-next-claim.model';
import { type NextClaim, passReasonWord } from './runner-next-claim.domain';
@Component({
  selector: 'app-runner-next-claim-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [NgTemplateOutlet, KitAccordionSection, KitAsyncState, KitSectionHead, KitUrgencyPill, RouterLink, AppLink],
  hostDirectives: [KitAccordionGroup],
  templateUrl: './runner-next-claim-view.html',
  styleUrl: './runner-next-claim-view.css',
})
export class RunnerNextClaimView {
  readonly runnerId = input.required<string>();
  readonly data = input<NextClaim | null>(null);
  readonly state = input.required<KitAsyncStateValue>();
  readonly urgencies = input<readonly Urgency[]>([]);
  readonly urgencyIconUrls = input<Readonly<Record<string, string | undefined>>>({});
  readonly statuses = input<Readonly<Record<string, string | undefined>>>({});
  protected readonly expanded = signal(false);
  protected readonly compact = compactRef;
  protected readonly summary = nextClaimSummary;
  protected readonly reasonWord = passReasonWord;
  protected urgency(ref: UrgencyReference | null): Urgency | null {
    return this.urgencies().find((item) => item.id === ref?.id) ?? null;
  }
}
