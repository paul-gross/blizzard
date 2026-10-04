import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import type { ChunkDetail } from '../../api/hub';

@Component({
  selector: 'fleet-chunk-delivery',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './chunk-delivery.html',
  styleUrl: './chunk-delivery.css',
})
export class ChunkDelivery {
  readonly detail = input.required<ChunkDetail>();
}
