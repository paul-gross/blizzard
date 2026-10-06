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
      expect(el.querySelectorAll('[data-testid="delivery-row"]')).toHaveLength(3);
      expect(el.querySelector('[data-testid="chunk-delivery"]')?.textContent).toContain('local');
      expect(el.querySelector('[data-testid="delivery-merge-wait"]')).not.toBeNull();
      fixture.componentRef.setInput('detail', { ...DETAIL, awaiting_external_merge: false });
      await fixture.whenStable();
      expect(el.querySelector('[data-testid="delivery-merge-wait"]')).toBeNull();
    });
  }

  it('combines a repo\'s PR and landed commit into one row, with the sha cut to seven characters', async () => {
    const fixture = TestBed.createComponent(ChunkGeneralTab);
    fixture.componentRef.setInput('detail', {
      ...DETAIL,
      open_prs: [],
      closed_prs: [{ repo: 'widget', number: 42, url: 'https://forge.example/widget/pull/42' }],
      landed_repos: [{ repo: 'widget', commit_hash: 'abcdef0123456789', url: 'https://forge.example/widget/commit/abcdef0123456789' }],
    });
    await fixture.whenStable();
    const rows = (fixture.nativeElement as HTMLElement).querySelectorAll('[data-testid="delivery-row"]');
    expect(rows).toHaveLength(1);
    expect(rows[0].querySelector('[data-testid="delivery-closed-pr-link"]')?.textContent).toContain('#42');
    const sha = rows[0].querySelector<HTMLAnchorElement>('[data-testid="delivery-landed-link"]')!;
    expect(sha.textContent).toBe('abcdef0');
    expect(sha.href).toBe('https://forge.example/widget/commit/abcdef0123456789');
    expect(rows[0].textContent?.match(/widget/g)).toHaveLength(1);
  });
});
