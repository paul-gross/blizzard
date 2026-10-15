import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { asyncState, FleetWhen, KitAsyncState, KitBadge } from 'fleet';

import { injectHostRegistrationQuery } from './host.query';
import { hostRegistrationSummary } from './host-registration.model';

/**
 * The host's registration **container** — owns the host's registration read and its
 * async-state triad, folded into a display summary ({@link hostRegistrationSummary}):
 * the state, what the hub recorded for the host, and why when short of registered.
 */
@Component({
  selector: 'app-host-registration',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetWhen, KitAsyncState, KitBadge],
  templateUrl: './host-registration.html',
  styleUrl: './host-registration.css',
})
export class HostRegistration {
  protected readonly query = injectHostRegistrationQuery();

  protected readonly summary = computed(() => hostRegistrationSummary(this.query.data()));

  protected readonly triadState = computed(() => asyncState(this.query, this.summary() === null));
}
