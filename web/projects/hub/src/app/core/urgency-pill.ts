import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { type Urgency } from 'fleet/core';
import { KitUrgencyPill } from 'fleet/kit/tooltip';
import { injectUrgencyIconUrl } from './urgency-icon.query';

/** Presents an urgency using the authenticated icon when it has no emoji. */
@Component({
  selector: 'app-urgency-pill',
  imports: [KitUrgencyPill],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './urgency-pill.html',
})
export class UrgencyPill {
  readonly urgency = input.required<Urgency>();
  /** False inside a listbox row, where a focusable pill would add a Tab stop. */
  readonly focusable = input(true);
  protected readonly iconUrl = injectUrgencyIconUrl(() => {
    const urgency = this.urgency();
    return !urgency.emoji && urgency.icon ? { key: urgency.id, digest: urgency.icon.digest } : null;
  });
}
