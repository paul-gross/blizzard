import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { type KitAsyncStateValue } from 'fleet';
import { FIXTURE_NOW, fixtureInstant, settle } from 'fleet/testing';
import { DeliveryGlanceView } from './delivery-glance-view';
import type { DeliveryList, DeliveryRow } from './delivery.model';

const row = (chunkId: string, fields: Partial<DeliveryRow> = {}): DeliveryRow => ({
  chunk: { chunkId, shortId: chunkId, lines: [], titlesPending: false, tint: null, projectName: null },
  at: fixtureInstant(-60),
  stage: 0,
  bounce: null,
  line: null,
  step: null,
  links: [],
  icon: { lane: 'running', status: 'running' },
  statusColor: '',
  ...fields,
});

const LIST: DeliveryList = {
  delivering: [row('ch_pr'), row('ch_bounced', { bounce: { cause: 'conflict', withdrawn: false, reason: null, count: 1, title: '', at: fixtureInstant(-60) } })],
  delivered: [row('ch_new', { stage: 3, at: fixtureInstant(-3600) }), row('ch_old', { stage: 3, at: fixtureInstant(-49 * 3600) })],
};

describe('DeliveryGlanceView', () => {
  beforeEach(() => TestBed.configureTestingModule({ imports: [DeliveryGlanceView], providers: [provideZonelessChangeDetection(), provideRouter([])] }));

  async function render(state: KitAsyncStateValue, list: DeliveryList = LIST) {
    const fixture = TestBed.createComponent(DeliveryGlanceView);
    fixture.componentRef.setInput('state', state);
    fixture.componentRef.setInput('list', list);
    fixture.componentRef.setInput('now', FIXTURE_NOW);
    await settle(fixture);
    return { fixture, el: fixture.nativeElement as HTMLElement };
  }

  const cards = (el: HTMLElement) => [...el.querySelectorAll('[data-testid="delivery-card"]')].map((card) => card.getAttribute('data-chunk'));

  it('cards the chunks delivering over those delivered in the done window, counting each', async () => {
    const { el } = await render('ready');

    expect(cards(el)).toEqual(['ch_pr', 'ch_bounced', 'ch_new']);
    expect(el.querySelector('[data-testid="delivery-filter-delivering"] .v-n')?.textContent).toBe('2');
    expect(el.querySelector('[data-testid="delivery-filter-recent"] .v-n')?.textContent).toBe('1');
    expect(el.querySelector('[data-chunk="ch_bounced"] .stage')?.textContent?.trim()).toBe('conflict');
  });

  it('narrows to one group on its tile, and back on a second press', async () => {
    const { el, fixture } = await render('ready');
    const tile = el.querySelector<HTMLButtonElement>('[data-testid="delivery-filter-recent"]')!;

    tile.click();
    await settle(fixture);
    expect(cards(el)).toEqual(['ch_new']);

    tile.click();
    await settle(fixture);
    expect(cards(el)).toEqual(['ch_pr', 'ch_bounced', 'ch_new']);
  });

  it('draws its read state through the kit: a skeleton, the error, or the empty copy', async () => {
    expect((await render('loading')).el.querySelector('fleet-kit-skeleton')).not.toBeNull();
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ imports: [DeliveryGlanceView], providers: [provideZonelessChangeDetection(), provideRouter([])] });
    expect((await render('error')).el.querySelector('[data-testid="delivery-error"]')?.textContent).toContain("Failed to read the hub's flows.");
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ imports: [DeliveryGlanceView], providers: [provideZonelessChangeDetection(), provideRouter([])] });
    expect((await render('empty', { delivering: [], delivered: [] })).el.querySelector('[data-testid="delivery-empty"]')).not.toBeNull();
  });
});
