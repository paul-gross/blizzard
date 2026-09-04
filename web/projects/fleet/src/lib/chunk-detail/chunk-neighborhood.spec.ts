import { provideZonelessChangeDetection } from '@angular/core';
import { type ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import type { ChunkDetail, ChunkNeighborhoodView } from '../api/hub';
import { ChunkNeighborhood } from './chunk-neighborhood';

const PREREQUISITE_ID = 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9';
const DEPENDENT_ID = 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJA';

const BASE_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01subject00000000000000000',
  graph_id: 'gr_1',
  status: 'ready',
  current_node_id: null,
  latest_epoch: null,
  work_refs: [],
  history: [],
  artifacts: [],
};

const EMPTY: ChunkNeighborhoodView = { prerequisites: [], dependents: [] };

const ONE_OF_EACH: ChunkNeighborhoodView = {
  prerequisites: [{ chunk_id: PREREQUISITE_ID, status: 'not_ready', satisfied: false }],
  dependents: [{ chunk_id: DEPENDENT_ID, status: 'ready', satisfied: false }],
};

const SEVERAL: ChunkNeighborhoodView = {
  prerequisites: [
    { chunk_id: 'ch_01aaaaaaaaaaaaaaaaaaaaaaaa', status: 'done', satisfied: true },
    { chunk_id: 'ch_01bbbbbbbbbbbbbbbbbbbbbbbb', status: 'not_ready', satisfied: false },
    { chunk_id: 'ch_01ccccccccccccccccccccccc', status: null, satisfied: false },
  ],
  dependents: [
    { chunk_id: 'ch_01ddddddddddddddddddddddd', status: 'ready', satisfied: true },
    { chunk_id: 'ch_01eeeeeeeeeeeeeeeeeeeeeee', status: 'running', satisfied: true },
  ],
};

async function render(
  neighborhood: ChunkNeighborhoodView,
  asLink = false,
): Promise<{ fixture: ComponentFixture<ChunkNeighborhood>; el: HTMLElement }> {
  await TestBed.configureTestingModule({
    imports: [ChunkNeighborhood],
    providers: [provideZonelessChangeDetection(), provideRouter([])],
  }).compileComponents();
  const fixture = TestBed.createComponent(ChunkNeighborhood);
  fixture.componentRef.setInput('detail', { ...BASE_DETAIL, neighborhood });
  if (asLink) fixture.componentRef.setInput('asLink', true);
  await fixture.whenStable();
  return { fixture, el: fixture.nativeElement as HTMLElement };
}

describe('ChunkNeighborhood', () => {
  it('renders both empty-state placeholders for a chunk with neither neighbor', async () => {
    const { el } = await render(EMPTY);

    expect(el.querySelector('[data-testid="neighborhood-prerequisites-empty"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="neighborhood-dependents-empty"]')).not.toBeNull();
    expect(el.querySelectorAll('[data-testid="neighbor"]')).toHaveLength(0);
  });

  it('renders one prerequisite and one dependent', async () => {
    const { el } = await render(ONE_OF_EACH);

    const prerequisites = el.querySelector('[data-testid="neighborhood-prerequisites"]')!;
    const dependents = el.querySelector('[data-testid="neighborhood-dependents"]')!;
    expect(prerequisites.querySelectorAll('[data-testid="neighbor"]')).toHaveLength(1);
    expect(dependents.querySelectorAll('[data-testid="neighbor"]')).toHaveLength(1);
    expect(prerequisites.textContent).toContain('not_ready');
    expect(dependents.textContent).toContain('ready');
  });

  it('renders several neighbors in each direction', async () => {
    const { el } = await render(SEVERAL);

    const prerequisites = el.querySelector('[data-testid="neighborhood-prerequisites"]')!;
    const dependents = el.querySelector('[data-testid="neighborhood-dependents"]')!;
    expect(prerequisites.querySelectorAll('[data-testid="neighbor"]')).toHaveLength(3);
    expect(dependents.querySelectorAll('[data-testid="neighbor"]')).toHaveLength(2);
  });

  it('names an unresolvable neighbor as unknown rather than dropping it', async () => {
    const { el } = await render(SEVERAL);

    const prerequisites = el.querySelector('[data-testid="neighborhood-prerequisites"]')!;
    expect(prerequisites.textContent).toContain('unknown');
  });

  it('marks a satisfied edge and an unmet one distinctly in the rendered text', async () => {
    const { el } = await render(SEVERAL);

    const badges = Array.from(el.querySelectorAll('[data-testid="neighbor-satisfied"]')).map((b) => b.textContent?.trim());
    expect(badges).toContain('satisfied');
    expect(badges).toContain('unmet');
  });

  it('exposes no control that declares, releases, or edits an edge', async () => {
    const { el } = await render(SEVERAL);

    expect(el.querySelectorAll('input, select, textarea')).toHaveLength(0);
  });

  it('renders a dock-select button, not a link, when asLink is false (the default)', async () => {
    const { el } = await render(ONE_OF_EACH);

    expect(el.querySelector('button[data-testid="neighbor"]')).not.toBeNull();
    expect(el.querySelector('a[data-testid="neighbor"]')).toBeNull();
  });

  it('emits selectChunk with the neighbor id when the dock-select button is clicked', async () => {
    const { fixture, el } = await render(ONE_OF_EACH);
    let emitted: string | undefined;
    fixture.componentInstance.selectChunk.subscribe((chunkId) => (emitted = chunkId));

    el.querySelector<HTMLButtonElement>('[data-testid="neighborhood-prerequisites"] [data-testid="neighbor"]')?.click();

    expect(emitted).toBe(PREREQUISITE_ID);
  });

  it('renders a routerLink under linkBase, not a button, when asLink is true', async () => {
    const { el } = await render(ONE_OF_EACH, true);

    const link = el.querySelector<HTMLAnchorElement>('[data-testid="neighborhood-prerequisites"] a[data-testid="neighbor"]');
    expect(link).not.toBeNull();
    expect(link?.getAttribute('href')).toBe(`/board/chunk/${PREREQUISITE_ID}`);
    expect(el.querySelector('button[data-testid="neighbor"]')).toBeNull();
  });
});
