import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page, userEvent } from 'vitest/browser';

import { KitTooltip } from './kit-tooltip';

/**
 * `KitTooltip`'s own half of `web:shell-sweep`
 * (`blizzard-context:/verification/blizzard.md` bzh:web-shell-sweep) — a real
 * hover and a real keyboard focus, neither of which jsdom resolves against a
 * pointer or a genuine tab order, plus the `aria-describedby`/panel-id relationship
 * resolving against an actual element in the rendered DOM rather than a string
 * comparison of ids nothing else checks exist.
 *
 * Excluded from the default `ng test fleet` run (`angular.json`'s `test.exclude`)
 * because it needs `--browsers=ChromiumHeadless`, not jsdom — run it via
 * `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
@Component({
  imports: [KitTooltip],
  template: `<button type="button" [fleetTooltip]="'Ships the selected chunk'" data-testid="trigger">Ship</button>`,
})
class SweepHost {}

describe('KitTooltip shell sweep (web:shell-sweep)', () => {
  it('opens on real hover and real keyboard focus, and wires aria-describedby to the rendered panel', async () => {
    await page.viewport(1024, 768);
    await TestBed.configureTestingModule({
      imports: [SweepHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(SweepHost);
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      const trigger = root.querySelector<HTMLElement>('[data-testid="trigger"]')!;

      // A prior spec in the same real-browser session can leave the pointer sitting
      // wherever it last moved to; move it away first so this case's own starting
      // state is deterministic rather than inherited.
      await userEvent.unhover(trigger);
      await fixture.whenStable();

      // A real pointer hover opens the panel.
      expect(document.querySelector('[role="tooltip"]')).toBeNull();
      await userEvent.hover(trigger);
      await fixture.whenStable();
      const hoveredPanel = document.querySelector<HTMLElement>('[role="tooltip"]');
      expect(hoveredPanel?.textContent?.trim()).toBe('Ships the selected chunk');

      // aria-describedby resolves to that same panel's real id in the DOM.
      const describedBy = trigger.getAttribute('aria-describedby');
      expect(describedBy).not.toBeNull();
      expect(document.getElementById(describedBy!)).toBe(hoveredPanel);

      await userEvent.unhover(trigger);
      await fixture.whenStable();
      expect(document.querySelector('[role="tooltip"]')).toBeNull();

      // A real Tab press focusing the trigger also opens it.
      await userEvent.tab();
      await fixture.whenStable();
      expect(document.activeElement).toBe(trigger);
      const focusedPanel = document.querySelector<HTMLElement>('[role="tooltip"]');
      expect(focusedPanel?.textContent?.trim()).toBe('Ships the selected chunk');
      expect(document.getElementById(trigger.getAttribute('aria-describedby')!)).toBe(focusedPanel);
    } finally {
      root.remove();
    }
  });
});
