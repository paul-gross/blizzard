import { ChangeDetectionStrategy, Component, input } from '@angular/core';

/** One resolved row for {@link LocalSubscriptionsView} — every value is already resolved
 * for display (including {@link sampledAgo}'s clock-driven text), so the view itself
 * injects nothing. `ok` drives the row's tone (`null` = never sampled, `false` = miss),
 * carried alongside the already-composed {@link conditionLabel} rather than re-derived from it.
 * `renewalLabel` is `null` whenever this attempt carries no renewal outcome — no renewer
 * for this slug's provider (e.g. Anthropic), or its renewal was not due (blizzard#504 Phase 2). */
export interface SubscriptionRow {
  readonly slug: string;
  readonly name: string;
  readonly provider: string;
  readonly conditionLabel: string;
  readonly sampledAgo: string;
  readonly renewalLabel: string | null;
  readonly ok: boolean | null;
}

/**
 * {@link LocalSubscriptions}'s presentational sibling (`bzh:frontend-container-presentational`):
 * plain inputs only, injects nothing, and owns the row template — the container keeps
 * the query, the resolved async-state triad, and the ticking clock {@link
 * SubscriptionRow.sampledAgo} is derived from.
 */
@Component({
  selector: 'local-subscriptions-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './local-subscriptions-view.html',
  styleUrl: './local-subscriptions-view.css',
})
export class LocalSubscriptionsView {
  readonly rows = input<readonly SubscriptionRow[]>([]);
}
