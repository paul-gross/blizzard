import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { KitChipOption } from './kit-chips';
import { KitSelect } from './kit-select';

const OPTIONS: readonly KitChipOption[] = [
  { value: 'all', label: 'All routines', testid: 'opt-all' },
  { value: 'a', label: 'Alpha', testid: 'opt-a' },
  { value: 'b', label: 'Bravo', testid: 'opt-b' },
  { value: 'locked', label: 'Locked', testid: 'opt-locked', disabled: true },
];

@Component({
  selector: 'fleet-test-host',
  imports: [KitSelect],
  template: `
    <fleet-kit-select
      ariaLabel="Routine filter"
      [label]="label()"
      testid="the-select"
      [options]="options"
      [selectedValue]="selected()"
      [disabled]="disabled()"
      (choose)="picks.push($event)"
    />
    <button type="button" data-testid="outside">outside</button>
  `,
})
class TestHost {
  options = OPTIONS;
  selected = signal<string | null>('a');
  label = signal<string | null>(null);
  disabled = signal(false);
  picks: string[] = [];
}

const inOverlay = (selector: string) => document.body.querySelector<HTMLElement>(selector);

/** The CDK reads the legacy `keyCode`, so a synthetic key event must carry it. */
const keydown = (target: Element | null, key: string, keyCode: number) =>
  target?.dispatchEvent(new KeyboardEvent('keydown', { key, keyCode, bubbles: true }));

describe('KitSelect', () => {
  let fixture: ReturnType<typeof TestBed.createComponent<TestHost>>;
  let el: HTMLElement;

  const trigger = () => el.querySelector<HTMLElement>('[data-testid="the-select"]');
  const open = async () => {
    trigger()?.click();
    await fixture.whenStable();
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    el = fixture.nativeElement as HTMLElement;
  });

  afterEach(() => fixture.destroy());

  it('shows the selected option label on the trigger and no popup until opened', () => {
    expect(trigger()?.textContent).toContain('Alpha');
    expect(trigger()?.getAttribute('aria-haspopup')).toBe('listbox');
    expect(trigger()?.getAttribute('aria-expanded')).toBe('false');
    expect(inOverlay('[role="listbox"]')).toBeNull();
  });

  it('opens a listbox of every option, marking the current one aria-selected', async () => {
    await open();

    expect(trigger()?.getAttribute('aria-expanded')).toBe('true');
    const listbox = inOverlay('[role="listbox"]');
    expect(listbox?.getAttribute('aria-label')).toBe('Routine filter');
    expect(trigger()?.getAttribute('aria-controls')).toBe(listbox?.id);
    const options = [...document.body.querySelectorAll('[role="option"]')];
    expect(options.map((o) => o.textContent?.trim())).toEqual(['All routines', 'Alpha', 'Bravo', 'Locked']);
    expect(inOverlay('[data-testid="opt-a"]')?.getAttribute('aria-selected')).toBe('true');
    expect(inOverlay('[data-testid="opt-b"]')?.getAttribute('aria-selected')).toBe('false');
  });

  it('moves focus to the current option on open', async () => {
    await open();
    await fixture.whenStable();

    expect(document.activeElement).toBe(inOverlay('[data-testid="opt-a"]'));
  });

  it('emits the picked value, closes, and returns focus to the trigger', async () => {
    await open();

    inOverlay('[data-testid="opt-b"]')?.click();
    await fixture.whenStable();

    expect(fixture.componentInstance.picks).toEqual(['b']);
    expect(inOverlay('[role="listbox"]')).toBeNull();
    expect(document.activeElement).toBe(trigger());
  });

  it('emits on a re-pick of the current option too', async () => {
    await open();

    inOverlay('[data-testid="opt-a"]')?.click();
    await fixture.whenStable();

    expect(fixture.componentInstance.picks).toEqual(['a']);
  });

  it('picks the focused option on Enter, re-pick included', async () => {
    await open();
    await fixture.whenStable();

    keydown(inOverlay('[data-testid="opt-a"]'), 'Enter', 13);
    await fixture.whenStable();

    expect(fixture.componentInstance.picks).toEqual(['a']);
    expect(inOverlay('[role="listbox"]')).toBeNull();
  });

  it('moves between options with the arrow keys, then picks with Enter', async () => {
    await open();
    await fixture.whenStable();

    keydown(inOverlay('[role="listbox"]'), 'ArrowDown', 40);
    await fixture.whenStable();
    expect(document.activeElement).toBe(inOverlay('[data-testid="opt-b"]'));

    keydown(document.activeElement, 'Enter', 13);
    await fixture.whenStable();
    expect(fixture.componentInstance.picks).toEqual(['b']);
  });

  it('skips unavailable options by keyboard and refuses pointer and synthetic picks', async () => {
    await open();
    await fixture.whenStable();
    const locked = inOverlay('[data-testid="opt-locked"]');
    expect(locked?.getAttribute('aria-disabled')).toBe('true');
    locked?.click();
    keydown(locked, 'Enter', 13);
    await fixture.whenStable();
    expect(fixture.componentInstance.picks).toEqual([]);
    expect(trigger()?.getAttribute('aria-expanded')).toBe('true');

    keydown(inOverlay('[role="listbox"]'), 'ArrowDown', 40);
    expect(document.activeElement).toBe(inOverlay('[data-testid="opt-b"]'));
    keydown(inOverlay('[role="listbox"]'), 'ArrowDown', 40);
    expect(document.activeElement).not.toBe(locked);
  });

  it('disables the trigger and refuses to open', async () => {
    fixture.componentInstance.disabled.set(true);
    await fixture.whenStable();
    expect(trigger()?.hasAttribute('disabled')).toBe(true);
    trigger()?.click();
    await fixture.whenStable();
    expect(inOverlay('[role="listbox"]')).toBeNull();
  });

  it('closes on Escape without emitting, returning focus to the trigger', async () => {
    await open();

    keydown(inOverlay('[role="listbox"]'), 'Escape', 27);
    await fixture.whenStable();

    expect(inOverlay('[role="listbox"]')).toBeNull();
    expect(fixture.componentInstance.picks).toEqual([]);
    expect(document.activeElement).toBe(trigger());
  });

  it('closes on a backdrop click without emitting', async () => {
    await open();

    document.body.querySelector<HTMLElement>('.cdk-overlay-backdrop')?.click();
    await fixture.whenStable();

    expect(inOverlay('[role="listbox"]')).toBeNull();
    expect(fixture.componentInstance.picks).toEqual([]);
  });

  it('closes on a second trigger click', async () => {
    await open();
    await open();

    expect(inOverlay('[role="listbox"]')).toBeNull();
  });

  it('renders the label as a trigger prefix and folds it into the accessible name', async () => {
    fixture.componentInstance.label.set('Routine');
    await fixture.whenStable();

    expect(trigger()?.textContent).toContain('Routine');
    expect(trigger()?.getAttribute('aria-label')).toBe('Routine: Alpha');
  });

  it('names the trigger by ariaLabel and the selection when no label is set', () => {
    expect(trigger()?.getAttribute('aria-label')).toBe('Routine filter: Alpha');
  });

  it('shows the raw value, and names it, when selectedValue matches no option', async () => {
    fixture.componentInstance.selected.set('zz');
    await fixture.whenStable();

    expect(trigger()?.textContent).toContain('zz');
    expect(trigger()?.getAttribute('aria-label')).toBe('Routine filter: zz');
  });

  it('names the trigger by ariaLabel alone when nothing is selected', async () => {
    fixture.componentInstance.selected.set(null);
    await fixture.whenStable();

    expect(trigger()?.getAttribute('aria-label')).toBe('Routine filter');
  });

  it('tracks a changed selectedValue in the trigger label', async () => {
    fixture.componentInstance.selected.set('b');
    await fixture.whenStable();

    expect(trigger()?.textContent).toContain('Bravo');
  });
});
