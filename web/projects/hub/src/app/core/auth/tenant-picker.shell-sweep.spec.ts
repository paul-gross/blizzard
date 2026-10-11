import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import type { MeTenant } from 'fleet/shell';
import { page } from 'vitest/browser';

import { TenantPicker } from './tenant-picker';

/**
 * The tenant picker's half of `web:shell-sweep` — a real, headless-Chromium proof that every option spans the list at
 * one width, the widest name's, rather than sizing to its own name, and that a long name stays inside the viewport on
 * a phone. jsdom lays nothing out, so it cannot tell.
 *
 * Excluded from the default `ng test hub` run (`angular.json`'s `test.exclude`); run it via `npm run shell-sweep`.
 */
@Component({
  selector: 'app-test-tenant-picker',
  imports: [TenantPicker],
  template: `<app-tenant-picker [tenants]="tenants" />`,
})
class TestTenantPicker {
  readonly tenants: readonly MeTenant[] = [
    { tenant_id: 'ten_a', name: 'Acme', role: 'admin', permissions: ['fleet:view'] },
    { tenant_id: 'ten_globex', name: 'Globex Interplanetary Freight', role: 'guest', permissions: ['fleet:view'] },
    { tenant_id: 'ten_b', name: 'B', role: 'guest', permissions: ['fleet:view'] },
  ];
}

describe('tenant picker sweep (web:shell-sweep)', () => {
  it('gives every option the one width, inside the viewport, at every width', async () => {
    await TestBed.configureTestingModule({
      imports: [TestTenantPicker],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(TestTenantPicker);
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      for (const width of [1280, 390, 320]) {
        await page.viewport(width, 800);
        const options = [...root.querySelectorAll<HTMLElement>('[data-testid="tenant-picker-option"]')];
        expect(options, `three options at ${width}px`).toHaveLength(3);
        const boxes = options.map((option) => option.getBoundingClientRect());
        for (const box of boxes) {
          expect(box.width, `one option width at ${width}px`).toBeCloseTo(boxes[0].width, 0);
          expect(box.left, `one option edge at ${width}px`).toBeCloseTo(boxes[0].left, 0);
          expect(box.right, `inside the viewport at ${width}px`).toBeLessThanOrEqual(width);
        }
        expect(document.documentElement.scrollWidth, `no horizontal overflow at ${width}px`).toBeLessThanOrEqual(width);
      }
    } finally {
      root.remove();
    }
  });
});
