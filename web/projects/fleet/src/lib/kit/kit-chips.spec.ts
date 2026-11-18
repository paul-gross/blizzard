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
  template: `<fleet-kit-chips [options]="options" [selectedValue]="selected()" [disabled]="disabled()" (choose)="chosen = $event; choices.push($event)" />`,
})
class TestHost {
  options = OPTIONS;
  readonly selected = signal<string | null>(null);
  readonly disabled = signal(false);
  chosen: string | null = null;
  choices: string[] = [];
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
    const options = el.querySelectorAll('[role=option]');
    expect(chips[0].classList.contains('selected')).toBe(true);
    expect(chips[1].classList.contains('selected')).toBe(false);
    // Selection is conveyed to assistive tech, not just visually.
    expect(options[0].getAttribute('aria-selected')).toBe('true');
    expect(options[1].getAttribute('aria-selected')).toBe('false');

    (options[0] as HTMLElement).click();
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

  it('preserves no selection and emits every pointer pick even without a controlled update', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const options = (fixture.nativeElement as HTMLElement).querySelectorAll<HTMLElement>('[role=option]');
    expect(options[0].getAttribute('aria-selected')).toBe('false');
    options[0].click();
    await fixture.whenStable();
    options[0].click();
    expect(fixture.componentInstance.choices).toEqual(['a', 'a']);
  });

  it('emits keyboard changes even when returning to the controlled value', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.options = [{ value: 'a', label: 'A' }, { value: 'b', label: 'B' }];
    fixture.componentInstance.selected.set('a');
    await fixture.whenStable();
    const options = (fixture.nativeElement as HTMLElement).querySelectorAll<HTMLElement>('[role=option]');
    options[0].focus();
    options[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    options[1].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
    expect(fixture.componentInstance.choices).toEqual(['b', 'a']);
  });

});
