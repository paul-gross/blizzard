import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';

import { ChunkPage } from './chunk-page';

/**
 * The route-mounted host of {@link ChunkPage}. A hop between chunks changes only the `:chunkId` param, so the router
 * reuses the routed component; keying the page on the chunk makes each chunk its own visit, so the action port's
 * notices and pending state and the mobile focus origins never outlive the chunk they belong to.
 */
@Component({
  selector: 'fleet-chunk-page-route',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkPage],
  templateUrl: './chunk-page-route.html',
  styleUrl: './chunk-page-route.css',
})
export class ChunkPageRoute {
  private readonly route = inject(ActivatedRoute);
  private readonly params = toSignal(this.route.paramMap, { initialValue: this.route.snapshot.paramMap });
  protected readonly visit = computed(() => [this.params().get('chunkId')]);
}
