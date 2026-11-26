import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { injectHubChunkDetailQuery } from 'fleet/chunk-page';
import { chunkDetail, settle, stubHubClient } from 'fleet/testing';
import { hubChunkKey } from '../core/query-keys';
import { injectPrerequisiteStatuses } from './runner-next-claim.query';

@Component({ selector: 'app-prerequisite-cache-host', template: '' })
class PrerequisiteCacheHost {
  readonly showDetail = signal(false);
  readonly showPrerequisite = signal(false);
  readonly detail = injectHubChunkDetailQuery(() => this.showDetail() ? 'ch_1' : null);
  readonly statuses = injectPrerequisiteStatuses(() => this.showPrerequisite() ? ['ch_1'] : []);
}

describe('shared chunk detail and prerequisite cache', () => {
  it.each(['detail', 'prerequisite'] as const)('keeps raw tenant data when %s reads first', async (first) => {
    const wire = chunkDetail({ chunk_id: 'ch_1', status: 'running' });
    const stub = stubHubClient({ 'GET /api/chunks/{chunk_id}': () => wire });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    try {
      TestBed.configureTestingModule({
        providers: [provideZonelessChangeDetection(), provideTanStackQuery(client)],
      });
      const fixture = TestBed.createComponent(PrerequisiteCacheHost);
      if (first === 'detail') fixture.componentInstance.showDetail.set(true);
      else fixture.componentInstance.showPrerequisite.set(true);
      await settle(fixture);
      fixture.componentInstance.showDetail.set(true);
      fixture.componentInstance.showPrerequisite.set(true);
      await settle(fixture);

      expect(fixture.componentInstance.detail.data()?.status).toEqual({ kind: 'running' });
      expect(fixture.componentInstance.statuses()).toEqual({ ch_1: 'running' });
      expect(client.getQueryData(hubChunkKey('ch_1'))).toEqual(wire);
      expect(stub.forRoute('/api/chunks/{chunk_id}', 'GET').every((request) => !request.search.includes('project='))).toBe(true);
    } finally {
      stub.restore();
    }
  });
});
