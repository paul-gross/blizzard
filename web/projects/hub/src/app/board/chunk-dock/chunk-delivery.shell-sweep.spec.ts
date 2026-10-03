import { provideZonelessChangeDetection, type Type } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { page } from 'vitest/browser';

import { type ChunkDetail, type ChunkSummary, ChunkGeneralTab } from 'fleet';
import { BoardShell } from '../board-shell/board-shell';
import { ChunkDetailPanel } from './chunk-detail-panel';

const pr = { repo: 'very-long-repository-name', number: 42, url: 'https://forge.example/repository/pull/42' };
const landed = { repo: 'other-repository', commit_hash: 'a'.repeat(40), url: 'https://forge.example/other/commit/' + 'a'.repeat(40) };
const detail: ChunkDetail = {
  chunk_id: 'ch_delivery', graph_id: 'gr_1', status: 'delivering', current_node_id: null,
  latest_epoch: null, work_refs: [], history: [], artifacts: [],
  open_prs: [pr], landed_repos: [landed], awaiting_external_merge: true,
};
const summary: ChunkSummary = {
  chunk_id: detail.chunk_id, graph_id: detail.graph_id, status: detail.status,
  current_node_id: null, work_refs: [], open_prs: [pr], landed_repos: [landed], awaiting_external_merge: true,
};

describe('delivery links real Chromium shell sweep', () => {
  for (const width of [1280, 390]) {
    it(`mounts the board card, dock and routed General tab without clipping links at ${width}px`, async () => {
      await page.viewport(width, 800);
      TestBed.resetTestingModule();
      await TestBed.configureTestingModule({
        imports: [BoardShell, ChunkDetailPanel, ChunkGeneralTab],
        providers: [provideZonelessChangeDetection(), provideRouter([])],
      }).compileComponents();
      const fixtures = [BoardShell, ChunkDetailPanel, ChunkGeneralTab].map((component) =>
        TestBed.createComponent(component as Type<BoardShell | ChunkDetailPanel | ChunkGeneralTab>));
      const [board, dock, general] = fixtures;
      board.componentRef.setInput('chunks', [summary]);
      board.componentRef.setInput('state', 'ready');
      dock.componentRef.setInput('detail', detail);
      dock.componentRef.setInput('renderedStatus', detail.status);
      general.componentRef.setInput('detail', detail);
      const host = document.createElement('div');
      host.style.cssText = 'width:100%; max-width:100vw; min-width:0;';
      document.body.appendChild(host);
      for (const fixture of fixtures) {
        (fixture.nativeElement as HTMLElement).style.cssText = 'display:block; width:100%; min-width:0;';
        host.appendChild(fixture.nativeElement);
        await fixture.whenStable();
      }
      try {
        const card = host.querySelector<HTMLElement>('[data-testid="chunk-card"]')!;
        expect(card, 'board card did not mount').not.toBeNull();
        expect(card.querySelector('button a')).toBeNull();
        expect(card.querySelector('a')?.closest('button')).toBeNull();
        expect(host.querySelectorAll('[data-testid="chunk-delivery"]')).toHaveLength(2);
        expect(host.querySelectorAll('[data-testid="delivery-merge-wait"]')).toHaveLength(2);
        const links = host.querySelectorAll<HTMLAnchorElement>('[data-testid="card-pr-link"], [data-testid="delivery-pr-link"], [data-testid="delivery-landed-link"]');
        expect(links, 'the card renders its PR link and both detail views render both links').toHaveLength(5);
        for (const link of links) {
          expect(link.href).toContain('forge.example');
          expect(link.target).toBe('_blank');
          expect(link.rel).toContain('noopener');
          const rect = link.getBoundingClientRect();
          expect(rect.width, `${link.textContent} invisible at ${width}px`).toBeGreaterThan(0);
          expect(rect.right, `${link.textContent} overflows ${width}px`).toBeLessThanOrEqual(width + 1);
          expect(rect.left).toBeGreaterThanOrEqual(-1);
        }
        const closedOnly: ChunkDetail = {
          ...detail, open_prs: [], closed_prs: [pr], landed_repos: [], awaiting_external_merge: false,
        };
        dock.componentRef.setInput('detail', closedOnly);
        general.componentRef.setInput('detail', closedOnly);
        await Promise.all([dock.whenStable(), general.whenStable()]);
        const closedLinks = host.querySelectorAll<HTMLAnchorElement>('[data-testid="delivery-closed-pr-link"]');
        expect(closedLinks, 'closed-only delivery must remain visible in both detail views').toHaveLength(2);
        for (const link of closedLinks) {
          expect(link.href).toBe(pr.url);
          expect(link.textContent).toContain('Closed PR #42');
        }
      } finally {
        host.remove();
        fixtures.forEach((fixture) => fixture.destroy());
      }
    });
  }
});
