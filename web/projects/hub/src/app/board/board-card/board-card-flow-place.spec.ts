import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { BoardCard } from './board-card';
import { BoardCardComponent } from './board-card';
import { BoardListRow } from '../board-list/board-list-row';
import { placeLines } from '../flows/flow-place.model';

const DELIVERING: BoardCard = {
  chunkId: 'ch_01deliver000000000000000000',
  shortId: 'C-DLVR',
  status: 'delivering',
  terminal: false,
  promotable: false,
  node: 'deliver',
  nodeId: 'nd_deliver',
  workLines: [],
  itemsRead: true,
  costUsd: 0,
  costPartial: false,
  estimatedCostUsd: null,
  completedAt: null,
  blockedOn: null,
  blockedCount: 0,
  blockedOnStatus: null,
};

const PLACES = placeLines([
  {
    flow_id: 'flw_1', resource: 'repo_1:master', repository_name: 'blizzard', repository_slug: null,
    branch: 'master', state: 'landing', position: 1, next_poll_at: null,
  },
  {
    flow_id: 'flw_2', resource: 'repo_2:master', repository_name: 'blizzard-context', repository_slug: null,
    branch: 'master', state: 'waiting', position: 3, next_poll_at: null,
  },
]);

async function renderCard(card: BoardCard): Promise<HTMLElement> {
  await TestBed.configureTestingModule({ imports: [BoardCardComponent], providers: [provideZonelessChangeDetection()] }).compileComponents();
  const fixture = TestBed.createComponent(BoardCardComponent);
  fixture.componentRef.setInput('card', card);
  await fixture.whenStable();
  return fixture.nativeElement as HTMLElement;
}

async function renderRow(card: BoardCard): Promise<HTMLElement> {
  await TestBed.configureTestingModule({ imports: [BoardListRow], providers: [provideZonelessChangeDetection()] }).compileComponents();
  const fixture = TestBed.createComponent(BoardListRow);
  fixture.componentRef.setInput('card', card);
  fixture.componentRef.setInput('lane', 'running');
  await fixture.whenStable();
  return fixture.nativeElement as HTMLElement;
}

const textsOf = (el: HTMLElement, testid: string) =>
  [...el.querySelectorAll(`[data-testid="${testid}"]`)].map((line) => line.textContent?.trim());

describe('the place line on a delivering card', () => {
  it('shows one place per flow — landing on one, an ordinal on the other — under the card\'s delivery block', async () => {
    const el = await renderCard({ ...DELIVERING, flowPlaces: PLACES });
    expect(el.querySelector('[data-testid="card-delivery"]')).not.toBeNull();
    expect(textsOf(el, 'card-flow-place')).toEqual(['landing on blizzard/master', '3rd on blizzard-context/master']);
  });

  it('shows the delivery block for a place alone, with no pull request open and no merge awaited', async () => {
    const el = await renderCard({ ...DELIVERING, flowPlaces: PLACES.slice(1), openPrs: [], awaitingExternalMerge: false });
    expect(el.querySelector('[data-testid="card-delivery"]')).not.toBeNull();
  });

  it('shows no delivery block for a chunk with no place, pull request, or awaited merge', async () => {
    const el = await renderCard({ ...DELIVERING, flowPlaces: [] });
    expect(el.querySelector('[data-testid="card-delivery"]')).toBeNull();
  });
});

describe('the place line in a list row\'s delivery cell', () => {
  it('shows the same place per flow as the card does', async () => {
    const el = await renderRow({ ...DELIVERING, flowPlaces: PLACES });
    expect(textsOf(el, 'list-row-flow-place')).toEqual(['landing on blizzard/master', '3rd on blizzard-context/master']);
  });

  it('shows none for a chunk with no place', async () => {
    const el = await renderRow(DELIVERING);
    expect(el.querySelectorAll('[data-testid="list-row-flow-place"]')).toHaveLength(0);
  });
});
