import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { page } from 'vitest/browser';

import { FleetScopePanel, type ScopePanelVm } from './scope-panel';

/**
 * The gardening scope panel's layout (the tooled half of
 * `blizzard-context:/verification/blizzard.md`'s `web:shell-sweep` method) — a real, headless-Chromium proof that the
 * header holds the slug, the project chip, and Edit and Retire on one row at a desktop width and keeps them all on
 * screen at a phone width; that the Description and Last swept facts, the open description editor, and the "Tended by"
 * links stay inside the panel with no horizontal overflow; and that the loading, nothing-selected and no-tending-routine
 * states each render on their own at both widths.
 *
 * Excluded from the default `ng test hub` run (`angular.json`'s `test.exclude`) because it needs
 * `--browsers=ChromiumHeadless`, not jsdom — run it via `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
const VM: ScopePanelVm = {
  slug: 'blizzard',
  description: 'the blizzard monorepo — a long description that must wrap inside its own column rather than widen it',
  retired: false,
  renderedRetired: false,
  relatedRoutines: [
    { name: 'nightly', isDefault: true },
    { name: 'a-very-long-routine-name-that-should-wrap-rather-than-overflow-its-list', isDefault: false },
  ],
  swept: { at: '2026-03-05T00:00:00Z', notSwept: 'not in 28 days' },
};

/** Every root a sweep mounted, removed once it ends. */
const mounted: HTMLElement[] = [];

async function render(inputs: { vm: ScopePanelVm | null; state: 'loading' | 'empty' | 'ready' }) {
  await TestBed.configureTestingModule({
    imports: [FleetScopePanel],
    providers: [provideZonelessChangeDetection(), provideRouter([])],
  }).compileComponents();
  const fixture = TestBed.createComponent(FleetScopePanel);
  fixture.componentRef.setInput('state', inputs.state);
  fixture.componentRef.setInput('vm', inputs.vm);
  fixture.componentRef.setInput('project', 'Blizzard');
  fixture.componentRef.setInput('sameSlugIn', ['Winter']);
  fixture.componentRef.setInput('canEdit', true);
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
  mounted.push(root);
  await fixture.whenStable();
  return { fixture, root };
}

async function sweep(width: number, body: () => Promise<void>) {
  const pageErrors: string[] = [];
  const onError = (e: ErrorEvent) => pageErrors.push(e.message);
  const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
  window.addEventListener('error', onError);
  window.addEventListener('unhandledrejection', onRejection);
  try {
    await page.viewport(width, 900);
    await body();
  } finally {
    mounted.splice(0).forEach((root) => root.remove());
    window.removeEventListener('error', onError);
    window.removeEventListener('unhandledrejection', onRejection);
  }
  expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
}

const frame = () => new Promise((resolve) => requestAnimationFrame(resolve));

function onScreen(el: Element | null, width: number, what: string) {
  expect(el, what).not.toBeNull();
  const rect = el!.getBoundingClientRect();
  expect(rect.width, `${what} has no width`).toBeGreaterThan(0);
  expect(rect.right, `${what} leaves the viewport at ${width}px`).toBeLessThanOrEqual(width);
}

describe('gardening scope panel layout shell sweep (web:shell-sweep)', () => {
  it.each([1280, 390, 320])('lays out header, facts and tending routines with no horizontal overflow at %ipx', async (width) => {
    await sweep(width, async () => {
      const { fixture, root } = await render({ vm: VM, state: 'ready' });
      await frame();

      const panel = root.querySelector<HTMLElement>('[data-testid="gardening-scope-panel"]')!;
      const head = panel.querySelector<HTMLElement>('.sp-head')!;
      const slug = head.querySelector('h3')!;
      const chip = head.querySelector('[data-testid="gardening-scope-panel-project"]');
      const edit = head.querySelector('[data-testid="gardening-scope-panel-edit"]');
      const retire = head.querySelector('[data-testid="gardening-scope-panel-retire"]');
      for (const [el, what] of [[slug, 'slug'], [chip, 'project chip'], [edit, 'Edit'], [retire, 'Retire']] as const) {
        onScreen(el, width, what);
      }
      if (width >= 1280) {
        const top = slug.getBoundingClientRect().top;
        for (const el of [chip!, edit!, retire!]) {
          expect(Math.abs(el.getBoundingClientRect().top - top), 'the header is one row at a desktop width').toBeLessThan(12);
        }
        expect(edit!.getBoundingClientRect().left).toBeGreaterThan(chip!.getBoundingClientRect().right);
      }

      expect(root.querySelector('[data-testid="gardening-scope-panel-description"]')?.textContent).toContain('monorepo');
      expect(root.querySelector('[data-testid="gardening-scope-panel-last-swept"] fleet-when')).not.toBeNull();
      expect(root.querySelector('[data-testid="gardening-scope-panel-routines"] h4')?.textContent).toContain('Tended by');
      onScreen(root.querySelector('[data-testid="gardening-scope-panel-routine-nightly"]'), width, 'tending routine link');
      expect(root.querySelector('[data-testid="gardening-scope-panel-same-slug"]')).not.toBeNull();
      expect(panel.scrollWidth, `panel overflows at ${width}px`).toBeLessThanOrEqual(panel.clientWidth);

      (edit as HTMLButtonElement).click();
      await fixture.whenStable();
      await frame();
      onScreen(root.querySelector('[data-testid="gardening-scope-panel-description-input"]'), width, 'description editor');
      onScreen(root.querySelector('[data-testid="gardening-scope-panel-description-submit"]'), width, 'Set');
      expect(panel.scrollWidth, `panel overflows with the editor open at ${width}px`).toBeLessThanOrEqual(panel.clientWidth);
    });
  });

  it.each([1280, 390])('renders the loading and nothing-selected states on their own at %ipx', async (width) => {
    await sweep(width, async () => {
      const { fixture, root } = await render({ vm: null, state: 'loading' });
      await frame();
      expect(root.querySelector('[data-testid="gardening-scope-panel"]')).toBeNull();
      expect(root.querySelector('[data-testid="gardening-scope-panel-empty"]')).toBeNull();
      expect(root.querySelector('fleet-kit-async-state')?.textContent?.trim()).not.toBe('');

      fixture.componentRef.setInput('state', 'empty');
      await fixture.whenStable();
      await frame();
      onScreen(root.querySelector('[data-testid="gardening-scope-panel-empty"]'), width, 'nothing-selected state');
    });
  });

  it.each([1280, 390])('says no routine tends the scope, and that the window holds no sweep, at %ipx', async (width) => {
    await sweep(width, async () => {
      const { root } = await render({
        vm: { ...VM, relatedRoutines: [], swept: { at: null, notSwept: 'not in 28 days' } },
        state: 'ready',
      });
      await frame();
      onScreen(root.querySelector('[data-testid="gardening-scope-panel-routines-none"]'), width, 'no tending routine');
      expect(root.querySelector('[data-testid="gardening-scope-panel-last-swept"]')?.textContent).toContain(
        'not in 28 days',
      );
    });
  });
});
