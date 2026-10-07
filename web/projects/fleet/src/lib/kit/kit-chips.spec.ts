import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitChips, type KitChipOption } from './kit-chips';

const OPTIONS: KitChipOption[] = [
  { value: 'a', label: 'Option A', testid: 'chip-a' },
  { value: 'b', label: 'Option B', testid: 'chip-b', disabled: true },
];

@Component({
  selector: 'fleet-test-host',
  imports: [KitChips],
  template: `<fleet-kit-chips [options]="options" [selectedValue]="selected()" [action]="action()" [disabled]="disabled()" (choose)="chosen = $event" />`,
})
class TestHost {
  options = OPTIONS;
  readonly selected = signal<string | null>(null);
  readonly action = signal(false);
  readonly disabled = signal(false);
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

    (chips[0] as HTMLButtonElement).click();
    expect(fixture.componentInstance.chosen).toBe('a');
  });

  it('renders each chip fully rounded, matching the soft-pill vocabulary', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const chip = el.querySelector('.chip') as HTMLElement;
    expect(getComputedStyle(chip).borderRadius).toBe('999px');
  });

  it('forwards each option testid to its chip', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="chip-a"]')?.textContent?.trim()).toBe('Option A');
    expect(el.querySelector('[data-testid="chip-b"]')?.textContent?.trim()).toBe('Option B');
  });

  it('renders one-shot actions without pressed state and blocks disabled choices', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.action.set(true);
    fixture.componentInstance.selected.set('a');
    await fixture.whenStable();
    const chips = (fixture.nativeElement as HTMLElement).querySelectorAll<HTMLButtonElement>('.chip');
    expect(chips[0].hasAttribute('aria-pressed')).toBe(false);
    expect(chips[0].classList.contains('selected')).toBe(false);
    expect(chips[1].disabled).toBe(true);
    chips[1].click();
    expect(fixture.componentInstance.chosen).toBeNull();
    chips[0].click();
    expect(fixture.componentInstance.chosen).toBe('a');

    fixture.componentInstance.chosen = null;
    fixture.componentInstance.disabled.set(true);
    await fixture.whenStable();
    chips[0].click();
    expect(chips[0].disabled).toBe(true);
    expect(fixture.componentInstance.chosen).toBeNull();
  });
});
