import { provideZonelessChangeDetection } from '@angular/core';
import { type ComponentFixture, TestBed } from '@angular/core/testing';

import { DONE_LANE } from 'fleet';
import type { BoardCard } from '../board-card/board-card';
import { BoardList, type BoardListSection } from './board-list';

const CARD: BoardCard = {
  chunkId: 'ch_01running000000000000000',
  shortId: 'C-0001',
  status: 'running',
  terminal: false,
  promotable: false,
  node: 'build',
  nodeId: 'nd_build',
  pointerLabels: ['hub#1'],
  costUsd: 0,
  costPartial: false,
  estimatedCostUsd: null,
  completedAt: null,
  blockedOn: null,
  blockedCount: 0,
  blockedOnStatus: null,
};

const card = (suffix: string, fields: Partial<BoardCard> = {}): BoardCard => ({
  ...CARD,
  chunkId: `ch_01${suffix}`,
  shortId: `C-${suffix}`,
  ...fields,
});

const section = (key: string, cards: readonly BoardCard[]): BoardListSection => ({ key, label: key.toUpperCase(), count: null, cards });

describe('BoardList', () => {
  let fixture: ComponentFixture<BoardList>;
  let selected: string[];

  const render = async (sections: readonly BoardListSection[], loaded = true) => {
    await TestBed.configureTestingModule({
      imports: [BoardList],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    fixture = TestBed.createComponent(BoardList);
    fixture.componentRef.setInput('sections', sections);
    fixture.componentRef.setInput('loaded', loaded);
    selected = [];
    fixture.componentInstance.selectChunk.subscribe((id) => selected.push(id));
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  };

  const lanes = (el: HTMLElement) =>
    [...el.querySelectorAll('[data-testid="list-section"]')].map((s) => s.getAttribute('data-lane'));
  const rowsIn = (el: HTMLElement, lane: string) =>
    [...el.querySelectorAll(`[data-lane="${lane}"] [data-testid="list-row"]`)].map((r) => r.getAttribute('data-chunk'));
  const head = (el: HTMLElement, lane: string) =>
    el.querySelector<HTMLElement>(`[data-lane="${lane}"] [data-testid="accordion-section-head"]`)!;

  it('renders its sections in the order given, each with its own rows', async () => {
    const el = await render([section('needs', [card('a')]), section('running', [card('b'), card('c')])]);

    expect(lanes(el)).toEqual(['needs', 'running']);
    expect(rowsIn(el, 'running')).toEqual(['ch_01b', 'ch_01c']);
  });

  it('starts DONE folded, and opens it from its header', async () => {
    const el = await render([section(DONE_LANE, [card('d', { status: 'done', terminal: true })])]);
    expect(rowsIn(el, DONE_LANE)).toEqual([]);

    head(el, DONE_LANE).click();
    await fixture.whenStable();
    expect(rowsIn(el, DONE_LANE)).toEqual(['ch_01d']);
  });

  it('folds an open section from its header', async () => {
    const el = await render([section('running', [card('a')])]);
    head(el, 'running').click();
    await fixture.whenStable();

    expect(rowsIn(el, 'running')).toEqual([]);
  });

  it('says a section is empty only once the chunk read has answered', async () => {
    const unloaded = await render([section('ready', [])], false);
    expect(unloaded.querySelector('[data-testid="list-section-empty"]')).toBeNull();

    fixture.componentRef.setInput('loaded', true);
    await fixture.whenStable();
    expect(unloaded.querySelector('[data-testid="list-section-empty"]')?.textContent?.trim()).toBe('Nothing here');
  });

  describe('a row', () => {
    const titles = (el: HTMLElement) =>
      [...el.querySelectorAll('[data-testid="list-row-title"]')].map((t) => t.textContent?.trim());

    it("leads with its titles and a single item's description", async () => {
      const el = await render([section('running', [card('a', { titles: ['Migrate the event log'], description: 'Past 40M rows.' })])]);

      expect(titles(el)).toEqual(['Migrate the event log']);
      expect(el.querySelector('[data-testid="list-row-desc"]')?.textContent?.trim()).toBe('Past 40M rows.');
    });

    it('falls back to its first work ref while its titles are unread', async () => {
      const el = await render([section('running', [card('a', { titles: null })])]);

      expect(titles(el)).toEqual(['hub#1']);
    });

    it('folds a large group, and opens it without selecting the row', async () => {
      const el = await render([section('running', [card('a', { titles: ['1', '2', '3', '4'] })])]);
      expect(titles(el)).toEqual(['1', '2']);

      el.querySelector<HTMLElement>('[data-testid="list-row-fold"]')!.click();
      await fixture.whenStable();

      expect(titles(el)).toEqual(['1', '2', '3', '4']);
      expect(selected).toEqual([]);
    });

    it('opens its chunk through its own open control, named for the chunk', async () => {
      const el = await render([section('running', [card('a')])]);
      const open = el.querySelector<HTMLButtonElement>('[data-testid="list-row-open"]')!;

      expect(open.tagName).toBe('BUTTON');
      expect(open.getAttribute('aria-label')).toBe('Open chunk C-a');
      open.click();

      expect(selected).toEqual(['ch_01a']);
    });

    it('keeps its PR links and fold outside the open control, each its own target', async () => {
      const el = await render([
        section('running', [
          card('a', { titles: ['1', '2', '3', '4'], openPrs: [{ repo: 'blizzard', number: 7, url: 'https://example.test/pr/7' }] }),
        ]),
      ]);
      const open = el.querySelector('[data-testid="list-row-open"]')!;
      const link = el.querySelector<HTMLAnchorElement>('[data-testid="list-row"] a')!;
      link.addEventListener('click', (event) => event.preventDefault());

      link.click();

      expect(link.textContent?.trim()).toBe('#7');
      expect(open.contains(link)).toBe(false);
      expect(open.contains(el.querySelector('[data-testid="list-row-fold"]'))).toBe(false);
      expect(selected).toEqual([]);
    });
  });
});
