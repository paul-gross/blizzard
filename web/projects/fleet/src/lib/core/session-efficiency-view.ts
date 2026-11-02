import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { sessionEfficiencyParts, sessionEfficiencyTitle, type SessionEfficiencyInput } from './session-efficiency';

/** The session efficiency cost, wrapping between its parts and never inside one; its host carries the spelled-out
 * breakdown as its title. */
@Component({
  selector: 'fleet-session-efficiency',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '[attr.title]': 'title()' },
  templateUrl: './session-efficiency-view.html',
  styleUrl: './session-efficiency-view.css',
})
export class SessionEfficiency {
  readonly usage = input.required<SessionEfficiencyInput>();

  protected readonly parts = computed(() => sessionEfficiencyParts(this.usage()));
  protected readonly title = computed(() => sessionEfficiencyTitle(this.usage()));
}
