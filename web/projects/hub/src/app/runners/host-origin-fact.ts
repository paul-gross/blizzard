import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import type { HostOrigin } from './host-origin.model';

/** The host detail's hostname-matched navigation link, without a reachability claim. */
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
    return origin.kind === 'link' ? origin.href : null;
  }
}
