import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitConfirmDialog } from './kit-confirm-dialog';

@Component({
  imports: [KitConfirmDialog],
  template: `
    <fleet-kit-confirm-dialog
      [open]="isOpen"
      heading="Remove item"
      message="This cannot be undone."
      confirmLabel="Remove"
      variant="danger"
      testid="confirm-dialog"
      (confirmed)="confirmed += 1; isOpen = false"
      (cancelled)="cancelled += 1; isOpen = false"
    />
  `,
})
class Host {
  isOpen = true;
  confirmed = 0;
  cancelled = 0;
}

describe('KitConfirmDialog', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [Host],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('emits confirmed from its confirm control', async () => {
    const fixture = TestBed.createComponent(Host);
    await fixture.whenStable();

    (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="confirm-dialog-confirm"]')!.click();
    await fixture.whenStable();

    expect(fixture.componentInstance.confirmed).toBe(1);
    expect(fixture.componentInstance.cancelled).toBe(0);
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="confirm-dialog"]')).toBeNull();
  });

  it.each([
    ['Cancel', '[data-testid="confirm-dialog-cancel"]', false],
    ['Escape', '.scrim', true],
    ['backdrop', '.scrim', false],
  ])('emits cancelled from %s', async (_name, selector, escape) => {
    const fixture = TestBed.createComponent(Host);
    await fixture.whenStable();
    const target = (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>(selector)!;

    if (escape) {
      target.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    } else {
      target.click();
    }
    await fixture.whenStable();

    expect(fixture.componentInstance.confirmed).toBe(0);
    expect(fixture.componentInstance.cancelled).toBe(1);
    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="confirm-dialog"]')).toBeNull();
  });
});
