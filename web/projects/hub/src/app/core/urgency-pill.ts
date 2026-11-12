import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { KitUrgencyPill, type UrgencyView } from 'fleet';
import { injectUrgencyIconUrl } from './urgency-icon.query';

/** Presents an urgency using the authenticated icon when it has no emoji. */
@Component({
  selector: 'app-urgency-pill',
  imports: [KitUrgencyPill],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './urgency-pill.html',
})
export class UrgencyPill {
  readonly urgency = input.required<UrgencyView>();
  protected readonly iconUrl = injectUrgencyIconUrl(() => {
    const urgency = this.urgency();
    return !urgency.emoji && urgency.icon ? { key: urgency.urgency_id, digest: urgency.icon.digest } : null;
  });
}
