import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page } from 'vitest/browser';

import { FleetFindingPanel, type FindingPanelVm } from './finding-panel';
import { FleetScopePanel, type ScopePanelVm } from './scope-panel';

/**
 * The two presentational panels the five-way gardening split added and that no
 * sweep mounted: `FleetFindingPanel` and `FleetScopePanel`. Both make real layout
 * claims their own CSS states in prose and jsdom cannot evaluate — jsdom never lays
 * anything out, so a `flex-wrap` that silently stopped wrapping would pass every
 * unit test in the suite (`bzh:visual-change-needs-a-render`).
 *
 * The finding panel's own claim (`finding-panel.css`'s `.fp-actions`): four
 * CTA-sized triage buttons "wrap onto as many rows as the column's own width forces
 * rather than spilling into a horizontal scroll". The scope panel's
 * (`scope-panel.css`'s `.sp-edit`): the description input takes the row's remaining
 * width beside its actions, `flex: 1; min-width: 0`, rather than pushing them out
 * of the panel.
 *
 * Both are swept at the gardening detail column's real narrow widths — gardening
 * sits in the hub's mobile bottom tab bar (`bzh:narrow-viewport-tier-rule`).
 *
 * Excluded from the default `ng test fleet` run (`angular.json`'s `test.exclude`) —
 * run it via `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
const FINDING: FindingPanelVm = {
  findingId: 'fnd_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
  findingClass: 'stale-docstring',
  locus: 'src/blizzard/hub/store/internal/a-rather-long-module-path/invoice_ledger_reconciliation.py:142',
  state: 'live',
  observedCount: 4,
  introducedRev: '4ba7ef06d9f1c2b3a4e5f60718293a4b5c6d7e8f',
  introducedAt: '2026-01-01T00:00:00Z',
  firstObservedAt: '2026-01-02T00:00:00Z',
  lastSeenAt: '2026-01-10T00:00:00Z',
  summary: 'Module docstring narrates the change history rather than stating the contract.',
  note: null,
  workItem: null,
};

const SCOPE: ScopePanelVm = {
  slug: 'blizzard',
  description: 'the hub, runner, CLI and board — a deliberately long description that must wrap inside its own column',
  retired: false,
  defaultingRoutineNames: ['nightly', 'weekly'],
};

/** A bounded column standing in for the gardening detail pane the panels mount in
 * (`.gf-detail` / `.gs-right`), so the wrap is forced by a real width rather than
 * by the viewport alone. */
@Component({
  selector: 'fleet-test-finding-panel-host',
  imports: [FleetFindingPanel],
  template: `
    <div class="col">
      <fleet-finding-panel [vm]="vm()" [state]="'ready'" [canControl]="true" />
    </div>
  `,
  styles: `
    .col {
      width: 100%;
      min-width: 0;
    }
  `,
})
class TestFindingPanelHost {
  readonly vm = signal<FindingPanelVm | null>(FINDING);
}

@Component({
  selector: 'fleet-test-scope-panel-host',
  imports: [FleetScopePanel],
  template: `
    <div class="col">
      <fleet-scope-panel [vm]="vm()" [state]="'ready'" [canEdit]="true" />
    </div>
  `,
  styles: `
    .col {
      width: 100%;
      min-width: 0;
    }
  `,
})
class TestScopePanelHost {
  readonly vm = signal<ScopePanelVm | null>(SCOPE);
}

async function mount<T>(host: new () => T) {
  await TestBed.configureTestingModule({
    imports: [host as never],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(host);
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
  await fixture.whenStable();
  return { fixture, root };
}

describe('gardening panels shell sweep (web:shell-sweep)', () => {
  it('wraps the finding panel’s triage buttons rather than overflowing, at 1280/390/320px', async () => {
    const { fixture, root } = await mount(TestFindingPanelHost);

    try {
      for (const width of [1280, 390, 320]) {
        await page.viewport(width, 800);
        await new Promise((resolve) => requestAnimationFrame(resolve));
        await fixture.whenStable();

        const actions = root.querySelector<HTMLElement>('[data-testid="gardening-finding-panel-actions"]');
        expect(actions, `${width}px: no action row in the DOM`).not.toBeNull();

        expect(
          actions!.scrollWidth,
          `${width}px: the action row scrolls horizontally (${actions!.scrollWidth} > ${actions!.clientWidth}) — it spilled instead of wrapping`,
        ).toBeLessThanOrEqual(actions!.clientWidth);

        // `fleet-kit-button`'s own host is `display: contents` (`kit-button.css`), so
        // it has no box of its own to measure — the laid-out element is the `<button>`
        // inside it, and measuring the host would silently compare zeroed rects.
        const buttons = Array.from(actions!.querySelectorAll<HTMLElement>('button'));
        expect(buttons.length, `${width}px: no triage buttons rendered`).toBeGreaterThan(0);
        for (const button of buttons) {
          expect(
            Math.round(button.getBoundingClientRect().right),
            `${width}px: a triage button extends past the panel column`,
          ).toBeLessThanOrEqual(Math.ceil(actions!.getBoundingClientRect().right) + 1);
        }
      }

      // The claim is that the row *wraps*, so at the narrowest width the buttons
      // must genuinely occupy more than one row — a single row that merely fits
      // would pass the overflow checks above while the rule had stopped working.
      await page.viewport(320, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));
      const tops = new Set(
        Array.from(
          root.querySelectorAll<HTMLElement>('[data-testid="gardening-finding-panel-actions"] button'),
        ).map((b) => Math.round(b.getBoundingClientRect().top)),
      );
      expect(tops.size, '320px: every triage button shares one row — the flex-wrap never engaged').toBeGreaterThan(1);
    } finally {
      root.remove();
      await page.viewport(1280, 800);
    }
  });

  it('keeps the scope panel’s description editor inside its column beside the actions, at 1280/390/320px', async () => {
    const { fixture, root } = await mount(TestScopePanelHost);

    try {
      for (const width of [1280, 390, 320]) {
        await page.viewport(width, 800);
        await new Promise((resolve) => requestAnimationFrame(resolve));
        await fixture.whenStable();

        const edit = root.querySelector<HTMLElement>('.sp-edit');
        expect(edit, `${width}px: no .sp-edit row in the DOM`).not.toBeNull();
        expect(
          edit!.scrollWidth,
          `${width}px: the edit row overflows its column (${edit!.scrollWidth} > ${edit!.clientWidth})`,
        ).toBeLessThanOrEqual(edit!.clientWidth);
      }
    } finally {
      root.remove();
      await page.viewport(1280, 800);
    }
  });
});
