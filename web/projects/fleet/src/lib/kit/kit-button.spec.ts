import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitButton } from './kit-button';

@Component({
  selector: 'fleet-test-host',
  imports: [KitButton],
  template: `
    <fleet-kit-button
      [variant]="variant()"
      [disabled]="disabled()"
      [testid]="'act'"
      [ariaLabel]="'Do the thing'"
      (click)="clicks = clicks + 1"
    >
      Go
    </fleet-kit-button>
  `,
})
class TestHost {
  readonly variant = signal<'default' | 'primary' | 'danger'>('default');
  readonly disabled = signal(false);
  clicks = 0;
}

describe('KitButton', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders a native button with the projected label and passed-through attributes', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const button = el.querySelector<HTMLButtonElement>('[data-testid="act"]');
    expect(button?.tagName).toBe('BUTTON');
    expect(button?.type).toBe('button');
    expect(button?.textContent?.trim()).toBe('Go');
    expect(button?.getAttribute('aria-label')).toBe('Do the thing');
  });

  it('lets a click on the host reach the caller — the click passes through by native bubbling', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="act"]')?.click();
    expect(fixture.componentInstance.clicks).toBe(1);
  });

  it('applies the primary/danger variant classes', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.variant.set('primary');
    await fixture.whenStable();
    let el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('.act')?.classList.contains('primary')).toBe(true);

    fixture.componentInstance.variant.set('danger');
    await fixture.whenStable();
    el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('.act')?.classList.contains('danger')).toBe(true);
  });

  it('disables the native button when disabled is set', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.disabled.set(true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector<HTMLButtonElement>('[data-testid="act"]')?.disabled).toBe(true);
  });
});
