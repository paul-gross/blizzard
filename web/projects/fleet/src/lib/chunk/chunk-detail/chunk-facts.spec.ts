import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideRouter, Router, RouterLink } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';

import type { ChunkDetail } from '../../api/hub';
import { chunkDetail, chunkNeighborhoodView, chunkNeighborView, chunkUsageTotalView } from 'fleet/testing';
import { ChunkFacts } from './chunk-facts';
import { APP_SCOPE, type InAppLink } from '../../core/app-scope';

@Component({
  imports: [ChunkFacts],
  template: `<fleet-chunk-detail-facts [detail]="detail" [boardChunkIds]="ids" />`,
})
class BoardFactsRoute {
  readonly ids = new Set(['ch_on']);
  readonly detail = chunkDetail({ neighborhood: chunkNeighborhoodView({
    prerequisites: [chunkNeighborView({ chunk_id: 'ch_on' })],
    dependents: [chunkNeighborView({ chunk_id: 'ch_off' })],
  }) });
}

@Component({ template: '' })
class StandaloneRoute {}

describe('ChunkFacts', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ChunkFacts],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
  });

  async function render(detail: ChunkDetail, chunkLinkBase?: readonly string[]): Promise<HTMLElement> {
    const fixture = TestBed.createComponent(ChunkFacts);
    fixture.componentRef.setInput('detail', detail);
    if (chunkLinkBase) fixture.componentRef.setInput('chunkLinkBase', chunkLinkBase);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  const parts = (el: HTMLElement): string[] =>
    [...el.querySelectorAll('[data-testid="chunk-efficiency"] .part')].map((p) => p.textContent ?? '');

  it("states the chunk's total session efficiency cost", async () => {
    const el = await render(
      chunkDetail({
        cost: chunkUsageTotalView({
          input_tokens: 20_000,
          cache_create_tokens: 200_000,
          output_tokens: 32_700,
          cache_read_tokens: 8_340_000,
          cost_usd: 0.3,
          cost_partial: false,
          estimated_cost_usd: null,
        }),
      }),
    );
    expect(parts(el)).toEqual(['$0.30', '· 220kI', '/ 32.7kO', '× 2.57%']);
  });

  it('reads `$0.00` alone for a chunk with no recorded usage', async () => {
    const el = await render(
      chunkDetail({
        cost: chunkUsageTotalView({
          cost_usd: 0,
          estimated_cost_usd: null,
          cost_partial: false,
          input_tokens: 0,
          output_tokens: 0,
          cache_read_tokens: 0,
          cache_create_tokens: 0,
        }),
      }),
    );
    expect(parts(el)).toEqual(['$0.00']);
  });

  it('renders no Depends on / Blocks box for a chunk with no edges', async () => {
    const el = await render(chunkDetail({ neighborhood: chunkNeighborhoodView() }));
    expect(el.querySelector('[data-testid="chunk-deps"]')).toBeNull();
  });

  it('names each neighbor by compact ref, linked to its chunk, with its lane icon', async () => {
    const el = await render(
      chunkDetail({
        neighborhood: chunkNeighborhoodView({
          prerequisites: [chunkNeighborView({ chunk_id: 'ch_01prereq00000000000000000', status: 'done', satisfied: true })],
          dependents: [
            chunkNeighborView({ chunk_id: 'ch_01dependent000000000000aa', status: 'not_ready', satisfied: false }),
            chunkNeighborView({ chunk_id: 'ch_01dependent000000000000bb', status: null, satisfied: false }),
          ],
        }),
      }),
      ['/board', 'chunk'],
    );

    const dependsOn = el.querySelector('[data-testid="fact-depends-on"]')!;
    expect(dependsOn.querySelector('.tag')?.textContent).toBe('Depends on');
    const prereq = dependsOn.querySelector<HTMLAnchorElement>('[data-testid="depends-on-neighbor"]')!;
    expect(prereq.textContent?.trim()).toBe('C-0000');
    expect(prereq.getAttribute('href')).toBe('/board/chunk/ch_01prereq00000000000000000');
    expect(prereq.getAttribute('title')).toBe('ch_01prereq00000000000000000');
    expect(prereq.querySelector('fleet-lane-icon')).not.toBeNull();

    const blocks = el.querySelector('[data-testid="fact-blocks"]')!;
    expect(blocks.querySelector('.tag')?.textContent).toBe('Blocks');
    const deps = [...blocks.querySelectorAll('[data-testid="blocks-neighbor"]')];
    expect(deps.map((d) => d.textContent?.trim())).toEqual(['C-00aa', 'C-00bb']);
    // A neighbor with no known status draws no icon.
    expect(deps[1].querySelector('fleet-lane-icon')).toBeNull();
  });

  it('reads `—` for the side that has no edge while the other has one', async () => {
    const el = await render(
      chunkDetail({
        neighborhood: chunkNeighborhoodView({ prerequisites: [chunkNeighborView({ chunk_id: 'ch_01prereq00000000000000000' })] }),
      }),
    );
    expect(el.querySelector('[data-testid="fact-blocks"] .empty')?.textContent).toBe('—');
    expect(el.querySelector('[data-testid="fact-depends-on"] .empty')).toBeNull();
  });

  it('merges an in-board neighbor selection and leaves off-board neighbors as standalone links', async () => {
    const fixture = TestBed.createComponent(ChunkFacts);
    fixture.componentRef.setInput('detail', chunkDetail({
      neighborhood: chunkNeighborhoodView({
        prerequisites: [chunkNeighborView({ chunk_id: 'ch_on' })],
        dependents: [chunkNeighborView({ chunk_id: 'ch_off' })],
      }),
    }));
    fixture.componentRef.setInput('boardChunkIds', new Set(['ch_on']));
    await fixture.whenStable();
    const links = fixture.debugElement.queryAll(By.directive(RouterLink)).map((el) => el.injector.get(RouterLink));
    expect(links[0].queryParamsHandling).toBe('merge');
    expect(links[0].queryParams).toEqual({ chunk: 'ch_on', step: null, at: null });
    expect(links[1].queryParamsHandling).toBeUndefined();
    expect(links[1].queryParams).toBeUndefined();
    expect(links[1].urlTree?.toString()).toBe('/board/chunk/ch_off');
  });
});

describe('ChunkFacts navigation', () => {
  it('selects an on-board neighbor in place and falls back to lensed standalone detail', async () => {
    const rewrite = (link: InAppLink): InAppLink =>
      Array.isArray(link) && typeof link[0] === 'string' && link[0].startsWith('/')
        ? ['/p', 'example', link[0].slice(1), ...link.slice(1)] : link;
    TestBed.configureTestingModule({ providers: [
      provideZonelessChangeDetection(),
      provideRouter([
        { path: 'p/example/board', component: BoardFactsRoute },
        { path: 'p/example/board/chunk/:id', component: StandaloneRoute },
      ]),
      { provide: APP_SCOPE, useValue: { path: rewrite, query: () => ({}) } },
    ] });
    const harness = await RouterTestingHarness.create('/p/example/board?layout=list&chunk=old&step=build&at=12&filter=ready');
    const router = TestBed.inject(Router);
    harness.routeNativeElement!.querySelector<HTMLElement>('[data-testid="depends-on-neighbor"]')!.click();
    await harness.fixture.whenStable();
    expect(router.url).toBe('/p/example/board?layout=list&chunk=ch_on&filter=ready');
    harness.routeNativeElement!.querySelector<HTMLElement>('[data-testid="blocks-neighbor"]')!.click();
    await harness.fixture.whenStable();
    expect(router.url).toBe('/p/example/board/chunk/ch_off');
  });
});
