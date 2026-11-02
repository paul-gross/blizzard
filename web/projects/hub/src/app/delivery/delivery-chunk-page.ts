import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { map } from 'rxjs';

import { KitBackBar } from 'fleet';
import { LensLink } from 'fleet/shell';
import { injectActiveFlowsQuery } from '../core/flows.query';
import { injectLaneRepositoriesQuery } from '../core/repositories.query';
import { DeliveryDrawer } from './delivery-drawer';

/**
 * `/delivery/chunk/:chunkId` — the phone's screen for one chunk's delivery, opened from a Delivery card: a back row to
 * the list over the desktop drawer's own content, laid out for a narrow screen.
 */
@Component({
  selector: 'app-delivery-chunk-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [DeliveryDrawer, KitBackBar, LensLink, RouterLink],
  templateUrl: './delivery-chunk-page.html',
  styleUrl: './delivery-chunk-page.css',
})
export class DeliveryChunkPage {
  private readonly route = inject(ActivatedRoute);
  private readonly flowsQuery = injectActiveFlowsQuery();
  private readonly repositoriesQuery = injectLaneRepositoriesQuery();

  protected readonly chunkId = toSignal(this.route.paramMap.pipe(map((params) => params.get('chunkId') ?? '')), { initialValue: '' });
  protected readonly flows = computed(() => this.flowsQuery.data() ?? []);
  protected readonly repositories = computed(() => this.repositoriesQuery.data() ?? []);
}
