import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import type { HostOrigin } from './host-origin.model';

/**
 * The host detail's URL fact: the origin the board verified as this host, today's plain link for a
 * runner that cannot be verified, `Unreachable` when no declared origin answered as the host, and a
 * pending note while the probe runs — never an unverified link in flight.
 */
@Component({
  selector: 'app-host-origin-fact',
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrls: ['./fleet-detail.css', './host-detail.css'],
  templateUrl: './host-origin-fact.html',
})
export class HostOriginFact {
  readonly origin = input.required<HostOrigin>();

  protected href(): string | null {
    const origin = this.origin();
    return origin.kind === 'verified' || origin.kind === 'plain' ? origin.href : null;
  }
}
