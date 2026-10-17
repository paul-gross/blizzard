import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { KitBadge } from 'fleet';

/**
 * A runner's one subscription by display name, flagged UNROSTERED when its host's roster does
 * not carry it.
 *
 * Presentational only.
 */
@Component({
  selector: 'app-runner-subscription',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBadge],
  templateUrl: './runner-subscription.html',
  styleUrl: './runner-subscription.css',
})
export class RunnerSubscription {
  readonly name = input.required<string>();
  readonly unrostered = input(false);
}
