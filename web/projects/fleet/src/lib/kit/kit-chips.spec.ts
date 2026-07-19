import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitChips, type KitChipOption } from './kit-chips';

const OPTIONS: KitChipOption[] = [
  { value: 'a', label: 'Option A', testid: 'chip-a' },
  { value: 'b', label: 'Option B', testid: 'chip-b' },
];

@Component({
  selector: 'fleet-test-host',
  imports: [KitChips],
  template: `<fleet-kit-chips [options]="options" [selectedValue]="selected()" (choose)="chosen = $event" />`,
})
class TestHost {
  options = OPTIONS;
  readonly selected = signal<string | null>(null);
  chosen: string | null = null;
}

describe('KitChips', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders one chip per option', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const chips = el.querySelectorAll('.chip');
    expect(chips).toHaveLength(2);
    expect(chips[0].textContent?.trim()).toBe('Option A');
    expect(chips[1].textContent?.trim()).toBe('Option B');
  });

  it('marks the selected option and emits choose with the clicked value', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.selected.set('a');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const chips = el.querySelectorAll('.chip');
    expect(chips[0].classList.contains('selected')).toBe(true);
    expect(chips[1].classList.contains('selected')).toBe(false);
    // Selection is conveyed to assistive tech, not just visually.
    expect(chips[0].getAttribute('aria-pressed')).toBe('true');
    expect(chips[1].getAttribute('aria-pressed')).toBe('false');

    (chips[1] as HTMLButtonElement).click();
    expect(fixture.componentInstance.chosen).toBe('b');
  });

  it('forwards each option testid to its chip', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="chip-a"]')?.textContent?.trim()).toBe('Option A');
    expect(el.querySelector('[data-testid="chip-b"]')?.textContent?.trim()).toBe('Option B');
  });
});
