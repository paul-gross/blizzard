import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { chunkSummary, settle, stubHubClient } from 'fleet/testing';
import { boardChunksReadOptions, injectDomainBoardChunksQuery, type BoardChunksRead } from '../core/chunks.query';
import { injectDeliveryChunksQuery } from '../delivery/delivery.query';
import { injectRunnerClaimsQuery } from '../runners/runner-claims.query';

@Component({
  selector: 'app-test-shared-chunks-query-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '',
})
class TestSharedChunksQueryHost {
  readonly board = injectDomainBoardChunksQuery();
  readonly delivery = injectDeliveryChunksQuery();
  readonly claims = injectRunnerClaimsQuery();
}

describe('shared chunks cache', () => {
  it('shares the tenant cache across board, delivery, and runner claims observers', async () => {
    const row = {
      ...chunkSummary({ chunk_id: 'ch_held', graph_id: 'gr_1', status: 'ready' }),
      runner_id: 'rn_1', workspace_id: 'ws_1', environment_count: 2,
    };
    const stub = stubHubClient({
      'GET /api/chunks': () => ({ chunks: [row], next_cursor: null, board_done_policy: null }),
    });
    try {
      TestBed.configureTestingModule({
        imports: [TestSharedChunksQueryHost],
        providers: [
          provideZonelessChangeDetection(),
          provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        ],
      });
      const fixture = TestBed.createComponent(TestSharedChunksQueryHost);
      await settle(fixture);

      expect(fixture.componentInstance.board.data()?.chunks[0]).toMatchObject({ chunk_id: 'ch_held', laneStatus: { kind: 'ready' } });
      expect(fixture.componentInstance.delivery.data()?.[0]).toMatchObject({ chunk_id: 'ch_held', runner_id: 'rn_1', laneStatus: { kind: 'ready' } });
      expect(fixture.componentInstance.claims.data()?.[0]).toMatchObject({ chunk_id: 'ch_held', runner_id: 'rn_1', workspace_id: 'ws_1', environment_count: 2 });
      expect(TestBed.inject(QueryClient).getQueryData<BoardChunksRead>(boardChunksReadOptions(null).queryKey)?.chunks[0]).toEqual(row);
      expect(stub.forRoute('/api/chunks', 'GET')).toHaveLength(1);
    } finally {
      stub.restore();
    }
  });
});
