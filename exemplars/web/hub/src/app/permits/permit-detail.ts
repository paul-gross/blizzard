// exemplar: bzh:frontend-container-presentational, bzh:frontend-empty-state-gated — a route-param read, resting until named.

import { ChangeDetectionStrategy, Component, computed } from '@angular/core';

import { type KitAsyncStateValue, restingAsyncState } from 'fleet';
import { injectRouteParam } from '../core/route-state';
import { PermitDetailView } from './permit-detail-view';
import { injectPermitQuery } from './permits.query';

/** `/permits/:permitId` — one permit and the newest change to it. */
@Component({
  selector: 'app-permit-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [PermitDetailView],
  templateUrl: './permit-detail.html',
})
export class PermitDetail {
  private readonly permitId = injectRouteParam('permitId');
  protected readonly permitQuery = injectPermitQuery(() => this.permitId());

  protected readonly state = computed<KitAsyncStateValue>(() =>
    restingAsyncState(this.permitId() === null, this.permitQuery, this.permitQuery.data() === undefined),
  );
}
