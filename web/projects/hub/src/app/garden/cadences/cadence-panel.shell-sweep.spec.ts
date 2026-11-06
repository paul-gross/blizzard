import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import type { ChoreView } from 'fleet';
import {
  gardenCadenceAttentionView,
  gardenCadenceErrandView,
  gardenCadenceView,
  gardenChoreView,
  gardenErrandRunView,
} from 'fleet/testing';
import { page } from 'vitest/browser';

import { type CadencePanelVm, FleetCadencePanel } from './cadence-panel';
import { cadencePanelVm } from './gardening-cadence-detail.model';

/**
 * The gardening cadence panel (`FleetCadencePanel`) at a desktop and a phone width — a real, headless-Chromium proof
 * that its header, rhythm and next moments, attention, and chores with their errands tables stack with no horizontal
 * overflow, that a long routine slug and a long attention reason wrap inside the panel, and that each errands table
 * scrolls inside its own box rather than widening the panel. jsdom lays out none of this, and gardening sits in the
 * hub's mobile bottom tab bar, so the phone width binds (`bzh:narrow-viewport-tier-rule`).
 *
 * Excluded from the default `ng test hub` run (`angular.json`'s `test.exclude`) — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
const CADENCE = gardenCadenceView({
  slug: 'evening',
  name: 'Evening sweep over every scope the project keeps',
  chores: [
    gardenChoreView({ note: 'the nightly docs pass, over every linked scope, promoting what it finds' }),
    gardenChoreView({
      chore_id: 'chr_2',
      key: 'samples',
      routine: 'a-routine-with-a-rather-long-slug-that-must-wrap-inside-its-row',
      scopes: ['web', 'hub', 'a-scope-the-project-does-not-hold'],
      mode: 'sampled' as ChoreView['mode'],
      promote: false,
    }),
  ],
  attention: [gardenCadenceAttentionView({ chore: 'samples', scope: 'a-scope-the-project-does-not-hold' })],
});

const ERRANDS = [
  gardenCadenceErrandView({ scope: 'blizzard', last_run: gardenErrandRunView() }),
  gardenCadenceErrandView({ scope: 'blizzard-context', state: 'running', due_at: null, last_run: gardenErrandRunView({ finished_at: null }) }),
  gardenCadenceErrandView({ chore: 'samples', scope: 'web', state: 'due' }),
  gardenCadenceErrandView({
    chore: 'samples',
    scope: 'a-scope-the-project-does-not-hold',
    state: 'stopped',
    due_at: null,
    attention: [gardenCadenceAttentionView({ chore: 'samples', scope: 'a-scope-the-project-does-not-hold' })],
  }),
];

const VM: CadencePanelVm = cadencePanelVm(CADENCE, null, ERRANDS)!;

async function render() {
  await TestBed.configureTestingModule({
    imports: [FleetCadencePanel],
    providers: [provideZonelessChangeDetection(), provideRouter([])],
  }).compileComponents();
  const fixture = TestBed.createComponent(FleetCadencePanel);
  fixture.componentRef.setInput('state', 'ready');
  fixture.componentRef.setInput('vm', VM);
  fixture.componentRef.setInput('canEdit', true);
  await fixture.whenStable();
  return fixture;
}

describe('gardening cadence panel layout shell sweep (web:shell-sweep)', () => {
  it.each([1280, 390])('stacks every block with no horizontal overflow at %ipx', async (width) => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    window.addEventListener('error', onError);

    const fixture = await render();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(width, 900);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const panel = root.querySelector<HTMLElement>('[data-testid="gardening-cadence-panel"]')!;
      expect(panel).not.toBeNull();
      const blocks = [
        'gardening-cadence-panel-facts',
        'gardening-cadence-panel-attention',
        'gardening-cadence-chore-nightly',
        'gardening-cadence-chore-samples',
      ].map((id) => root.querySelector<HTMLElement>(`[data-testid="${id}"]`)!);
      for (const block of blocks) expect(block).not.toBeNull();
      const tops = blocks.map((b) => b.getBoundingClientRect().top);
      expect(new Set(tops).size, `blocks did not stack — tops were ${tops.join(', ')}`).toBe(blocks.length);
      expect(root.querySelectorAll('[data-testid="gardening-cadence-panel-moment"]')).toHaveLength(5);

      expect(
        panel.scrollWidth,
        `panel overflows horizontally at ${width}px (${panel.scrollWidth} > ${panel.clientWidth})`,
      ).toBeLessThanOrEqual(panel.clientWidth);
      for (const id of ['gardening-cadence-chore-samples', 'gardening-cadence-panel-attention']) {
        const block = root.querySelector<HTMLElement>(`[data-testid="${id}"]`)!;
        expect(block.scrollWidth, `${id} overflows at ${width}px`).toBeLessThanOrEqual(block.clientWidth);
      }
      for (const wrap of Array.from(root.querySelectorAll<HTMLElement>('.cp-table-wrap'))) {
        expect(wrap.getBoundingClientRect().right).toBeLessThanOrEqual(panel.getBoundingClientRect().right + 1);
      }
      const retire = root.querySelector<HTMLElement>('[data-testid="gardening-cadence-panel-retire"]')!;
      expect(retire.getBoundingClientRect().right).toBeLessThanOrEqual(width);
      expect(pageErrors).toEqual([]);
    } finally {
      root.remove();
      window.removeEventListener('error', onError);
      await page.viewport(1280, 800);
    }
  });
});
