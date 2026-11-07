import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitCloseButton } from './kit-close-button';

@Component({
  selector: 'fleet-test-host',
  imports: [KitCloseButton],
  template: `<fleet-kit-close-button testid="close" [ariaLabel]="label()" (click)="clicks = clicks + 1" />`,
})
class TestHost {
  readonly label = signal('Close');
  clicks = 0;
}

describe('KitCloseButton', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('is a native type="button" named by its label, with a decorative drawn X', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const button = (fixture.nativeElement as HTMLElement).querySelector<HTMLButtonElement>('[data-testid="close"]');

    expect(button?.tagName).toBe('BUTTON');
    expect(button?.type).toBe('button');
    expect(button?.getAttribute('aria-label')).toBe('Close');
    expect(button?.classList.contains('icon')).toBe(true);
    expect(button?.textContent?.trim()).toBe('');
    expect(button?.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true');
  });

  it('takes a custom accessible name', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.label.set('Close the drawer');
    await fixture.whenStable();
    const button = (fixture.nativeElement as HTMLElement).querySelector('[data-testid="close"]');

    expect(button?.getAttribute('aria-label')).toBe('Close the drawer');
  });

  it('lets a click reach the host by bubbling', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();

    (fixture.nativeElement as HTMLElement).querySelector<HTMLButtonElement>('[data-testid="close"]')?.click();
    expect(fixture.componentInstance.clicks).toBe(1);
  });
});
