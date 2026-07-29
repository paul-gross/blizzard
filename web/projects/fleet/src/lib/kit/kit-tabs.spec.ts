import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitTabs, type KitTabOption } from './kit-tabs';

const OPTIONS: KitTabOption[] = [
  { value: 'general', label: 'General', testid: 'tab-general' },
  { value: 'artifacts', label: 'Artifacts', testid: 'tab-artifacts' },
];

@Component({
  selector: 'fleet-test-host',
  imports: [KitTabs],
  template: `<fleet-kit-tabs [options]="options" [activeValue]="active()" (choose)="chosen = $event" />`,
})
class TestHost {
  options = OPTIONS;
  readonly active = signal<string | null>(null);
  chosen: string | null = null;
}

describe('KitTabs', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders one tab per option', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const tabs = el.querySelectorAll('.tab');
    expect(tabs).toHaveLength(2);
    expect(tabs[0].textContent?.trim()).toBe('General');
    expect(tabs[1].textContent?.trim()).toBe('Artifacts');
  });

  it('marks the active option and emits choose with the clicked value', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.active.set('general');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const tabs = el.querySelectorAll('.tab');
    expect(tabs[0].classList.contains('active')).toBe(true);
    expect(tabs[1].classList.contains('active')).toBe(false);
    expect(tabs[0].getAttribute('aria-selected')).toBe('true');
    expect(tabs[1].getAttribute('aria-selected')).toBe('false');

    (tabs[1] as HTMLButtonElement).click();
    expect(fixture.componentInstance.chosen).toBe('artifacts');
  });

  it('marks the strip and each tab with their ARIA roles', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[role="tablist"]')).not.toBeNull();
    expect(el.querySelectorAll('[role="tab"]')).toHaveLength(2);
  });

  it('forwards each option testid to its tab', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="tab-general"]')?.textContent?.trim()).toBe('General');
    expect(el.querySelector('[data-testid="tab-artifacts"]')?.textContent?.trim()).toBe('Artifacts');
  });
});
