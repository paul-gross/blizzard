import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitDialog } from './kit-dialog';

@Component({
  imports: [KitDialog],
  template: `
    <fleet-kit-dialog [open]="isOpen" ariaLabel="Test dialog" testid="test-dialog" (closed)="closes += 1">
      <span fleetKitDialogHeader data-testid="header-content">Header</span>
      <p data-testid="body-content">Body content</p>
      <button fleetKitDialogFooter data-testid="footer-content" type="button">Footer</button>
    </fleet-kit-dialog>
  `,
})
class Host {
  isOpen = false;
  closes = 0;
}

describe('KitDialog', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [Host],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders nothing while closed', async () => {
    const fixture = TestBed.createComponent(Host);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="test-dialog"]')).toBeNull();
  });

  it('renders the header, body, and footer projections once open, with the dialog ARIA contract', async () => {
    const fixture = TestBed.createComponent(Host);
    fixture.componentInstance.isOpen = true;
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const panel = el.querySelector('[data-testid="test-dialog"]');
    expect(panel).not.toBeNull();
    expect(panel!.getAttribute('role')).toBe('dialog');
    expect(panel!.getAttribute('aria-modal')).toBe('true');
    expect(panel!.getAttribute('aria-label')).toBe('Test dialog');
    expect(el.querySelector('[data-testid="header-content"]')?.textContent).toBe('Header');
    expect(el.querySelector('[data-testid="body-content"]')?.textContent).toBe('Body content');
    expect(el.querySelector('[data-testid="footer-content"]')?.textContent).toBe('Footer');
  });

  it('emits closed on a backdrop click but not on a click inside the panel', async () => {
    const fixture = TestBed.createComponent(Host);
    fixture.componentInstance.isOpen = true;
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLElement>('[data-testid="body-content"]')!.click();
    await fixture.whenStable();
    expect(fixture.componentInstance.closes).toBe(0);

    el.querySelector<HTMLElement>('.scrim')!.click();
    await fixture.whenStable();
    expect(fixture.componentInstance.closes).toBe(1);
  });

  it('emits closed on Escape', async () => {
    const fixture = TestBed.createComponent(Host);
    fixture.componentInstance.isOpen = true;
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLElement>('.scrim')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await fixture.whenStable();

    expect(fixture.componentInstance.closes).toBe(1);
  });

  it('never closes itself — the open input still reflects the caller unless it flips it', async () => {
    const fixture = TestBed.createComponent(Host);
    fixture.componentInstance.isOpen = true;
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLElement>('.scrim')!.click();
    await fixture.whenStable();

    expect(fixture.componentInstance.isOpen).toBe(true);
    expect(el.querySelector('[data-testid="test-dialog"]')).not.toBeNull();
  });
});
