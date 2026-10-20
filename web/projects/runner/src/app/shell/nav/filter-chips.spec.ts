import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { FilterChips } from './filter-chips';
import type { FilterChoice } from './panel-filters.model';

const CHOICES: readonly FilterChoice[] = [
  { value: null, label: 'All', title: 'every runner', disabled: false, brake: null, color: null },
  { value: 'r1', label: 'r1', title: 'r1', disabled: false, brake: null, color: 'blue' },
  { value: 'r2', label: 'r2', title: 'r2 · paused: usage limit', disabled: false, brake: 'pause', color: null },
  { value: 'r3', label: 'r3', title: 'r3 · paused by hub', disabled: false, brake: 'stop', color: null },
  { value: 'r4', label: 'r4', title: 'r4 is halted', disabled: true, brake: null, color: null },
];

@Component({
  selector: 'app-test-host',
  imports: [FilterChips],
  template: `<app-filter-chips
    label="Runner"
    testid="runner-filter"
    [choices]="choices"
    [current]="current()"
    [compact]="compact()"
    (choose)="chosen.push($event)"
  />`,
})
class TestHost {
  readonly choices = CHOICES;
  readonly current = signal<string | null>(null);
  readonly compact = signal(false);
  readonly chosen: (string | null)[] = [];
}

async function render() {
  await TestBed.configureTestingModule({
    imports: [TestHost],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(TestHost);
  await fixture.whenStable();
  const el = fixture.nativeElement as HTMLElement;
  const chip = (value: string) => el.querySelector<HTMLButtonElement>(`[data-testid="runner-filter-${value}"]`)!;
  // The control hears the chip host's click — dispatched there so a disabled button's own
  // suppression of the event never masks the control's guard.
  const click = async (value: string) => {
    chip(value).closest('fleet-kit-chip')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await fixture.whenStable();
  };
  return { fixture, el, chip, click };
}

describe('FilterChips', () => {
  it('names its group by the label and renders a chip per choice, All keyed as all', async () => {
    const { el } = await render();
    const group = el.querySelector('[data-testid="runner-filter"]');

    expect(group?.getAttribute('role')).toBe('group');
    expect(group?.getAttribute('aria-label')).toBe('Runner');
    expect(
      Array.from(el.querySelectorAll('button')).map((button) => button.getAttribute('data-testid')),
    ).toEqual(['runner-filter-all', 'runner-filter-r1', 'runner-filter-r2', 'runner-filter-r3', 'runner-filter-r4']);
  });

  it('selects the current choice, and only it', async () => {
    const { fixture, chip } = await render();
    expect(chip('all').getAttribute('aria-pressed')).toBe('true');
    expect(chip('r1').getAttribute('aria-pressed')).toBe('false');

    fixture.componentInstance.current.set('r1');
    await fixture.whenStable();

    expect(chip('all').getAttribute('aria-pressed')).toBe('false');
    expect(chip('r1').getAttribute('aria-pressed')).toBe('true');
  });

  it('emits the chosen value, null for All', async () => {
    const { fixture, click } = await render();
    fixture.componentInstance.current.set('r1');
    await fixture.whenStable();

    await click('r2');
    await click('all');

    expect(fixture.componentInstance.chosen).toEqual(['r2', null]);
  });

  it('emits nothing for the choice already current', async () => {
    const { fixture, click } = await render();

    await click('all');

    expect(fixture.componentInstance.chosen).toEqual([]);
  });

  it('offers a disabled choice disabled, and emits nothing for it', async () => {
    const { fixture, chip, click } = await render();

    expect(chip('r4').disabled).toBe(true);
    await click('r4');

    expect(fixture.componentInstance.chosen).toEqual([]);
  });

  it('shows a braked choice’s glyph beside its name, its words only in the title', async () => {
    const { chip } = await render();

    const paused = chip('r2').querySelector('[data-testid="runner-brake"]');
    expect(paused?.getAttribute('data-glyph')).toBe('pause');
    expect(paused?.getAttribute('title')).toBe('r2 · paused: usage limit');
    expect(chip('r2').textContent?.trim()).toBe('r2');
    expect(chip('r2').getAttribute('title')).toBe('r2 · paused: usage limit');

    expect(chip('r3').querySelector('[data-testid="runner-brake"]')?.getAttribute('data-glyph')).toBe('stop');
    expect(chip('r1').querySelector('[data-testid="runner-brake"]')).toBeNull();
  });

  it('shows a choice’s colour as a swatch before its name, and none for a choice without one', async () => {
    const { chip } = await render();
    expect(chip('r1').querySelector('fleet-kit-swatch')).not.toBeNull();
    expect(chip('r1').textContent?.trim()).toBe('r1');
    expect(chip('r2').querySelector('fleet-kit-swatch')).toBeNull();
  });

  it('marks itself compact when it sits as its own row', async () => {
    const { fixture, el } = await render();
    const host = el.querySelector('app-filter-chips')!;
    expect(host.classList.contains('compact')).toBe(false);

    fixture.componentInstance.compact.set(true);
    await fixture.whenStable();

    expect(host.classList.contains('compact')).toBe(true);
  });
});
