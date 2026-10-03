import { provideZonelessChangeDetection, type Type } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { type ChunkDetail, ChunkGeneralTab } from 'fleet';
import { ChunkDetailPanel } from './chunk-detail-panel';

const DETAIL: ChunkDetail = {
  chunk_id: 'ch_delivery', graph_id: 'gr_1', status: 'delivering', current_node_id: null,
  latest_epoch: null, work_refs: [], history: [], artifacts: [],
  open_prs: [{ repo: 'widget', number: 42, url: 'https://forge.example/widget/pull/42' }],
  landed_repos: [
    { repo: 'service', commit_hash: 'abc123', url: 'https://forge.example/service/commit/abc123' },
    { repo: 'local', commit_hash: 'def456', url: null },
  ],
  awaiting_external_merge: true,
};

describe('chunk delivery in both detail views', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ChunkGeneralTab, ChunkDetailPanel],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
  });

  for (const component of [ChunkGeneralTab, ChunkDetailPanel]) {
    it(`${component.name} shows per-repo links, fallback text and explicit wait`, async () => {
      const fixture = TestBed.createComponent(component as Type<ChunkDetailPanel | ChunkGeneralTab>);
      fixture.componentRef.setInput('detail', DETAIL);
      if (component === ChunkDetailPanel) fixture.componentRef.setInput('renderedStatus', DETAIL.status);
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;
      expect(el.querySelector<HTMLAnchorElement>('[data-testid="delivery-pr-link"]')?.href).toBe('https://forge.example/widget/pull/42');
      expect(el.querySelector<HTMLAnchorElement>('[data-testid="delivery-landed-link"]')?.href).toBe('https://forge.example/service/commit/abc123');
      expect(el.querySelector('[data-testid="delivery-landed-text"]')?.textContent).toBe('def456');
      expect(el.querySelector('[data-testid="chunk-delivery"]')?.textContent).toContain('local');
      expect(el.querySelector('[data-testid="delivery-merge-wait"]')).not.toBeNull();
      fixture.componentRef.setInput('detail', { ...DETAIL, awaiting_external_merge: false });
      await fixture.whenStable();
      expect(el.querySelector('[data-testid="delivery-merge-wait"]')).toBeNull();
    });
  }
});
