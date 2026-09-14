import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page, userEvent } from 'vitest/browser';

import { KitConfirmDialog } from './kit-confirm-dialog';

@Component({
  imports: [KitConfirmDialog],
  template: `
    <button data-testid="outside">Outside</button>
    <fleet-kit-confirm-dialog
      [open]="true"
      heading="Confirm the change"
      message="This action changes the selected item."
      confirmLabel="Confirm"
      variant="danger"
      testid="confirm-dialog"
    />
  `,
})
class SweepHost {}

describe('KitConfirmDialog shell sweep (web:shell-sweep)', () => {
  it('lays out its controls in the dialog footer and captures focus inside the confirmation', async () => {
    await page.viewport(1024, 768);
    await TestBed.configureTestingModule({
      imports: [SweepHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(SweepHost);
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();
    await new Promise((resolve) => requestAnimationFrame(resolve));

    try {
      const panel = root.querySelector<HTMLElement>('[data-testid="confirm-dialog"]')!;
      const footer = root.querySelector<HTMLElement>('.p-ftr')!;
      const cancel = root.querySelector<HTMLElement>('[data-testid="confirm-dialog-cancel"]')!;
      const confirm = root.querySelector<HTMLElement>('[data-testid="confirm-dialog-confirm"]')!;

      expect(footer.contains(cancel)).toBe(true);
      expect(footer.contains(confirm)).toBe(true);
      expect(cancel.getBoundingClientRect().top).toBeCloseTo(confirm.getBoundingClientRect().top, 0);
      expect(panel.contains(document.activeElement)).toBe(true);
      for (let i = 0; i < 6; i += 1) {
        await userEvent.tab();
        expect(panel.contains(document.activeElement)).toBe(true);
      }
    } finally {
      root.remove();
    }
  });
});
