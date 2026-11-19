import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { FormField, form } from '@angular/forms/signals';

import { type ComboOption, KitCombobox, NOT_IN_LIST } from './kit-combobox';

const OPTIONS: readonly ComboOption[] = [
  { value: 'America/Chicago', label: 'America/Chicago', meta: 'GMT-6' },
  { value: 'Europe/Berlin', label: 'Europe/Berlin', meta: 'GMT+1' },
  { value: 'Europe/Paris', label: 'Europe/Paris', meta: 'GMT+1' },
  { value: 'UTC', label: 'UTC', meta: 'GMT' },
];

@Component({
  selector: 'fleet-test-host',
  imports: [FormField, KitCombobox],
  template: `<fleet-kit-combobox [formField]="f" [options]="options" [noneLabel]="none()" ariaLabel="Zone" testid="zone" />`,
})
class Host {
  options = OPTIONS;
  none = signal<string | null>(null);
  draft = signal('UTC');
  f = form(this.draft);
}

const keydown = (target: Element, key: string) =>
  target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));

describe('KitCombobox', () => {
  let fixture: ReturnType<typeof TestBed.createComponent<Host>>;
  let host: Host;

  const input = () => fixture.nativeElement.querySelector('[data-testid="zone"]') as HTMLInputElement;
  const rows = () => [...document.body.querySelectorAll<HTMLElement>('[role="option"]')];
  const labels = () => rows().map((r) => r.querySelector('.label')?.textContent?.trim());
  const type = async (text: string) => {
    input().focus();
    input().value = text;
    input().dispatchEvent(new Event('input', { bubbles: true }));
    await fixture.whenStable();
  };
  const key = async (k: string) => {
    keydown(input(), k);
    await fixture.whenStable();
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [Host], providers: [provideZonelessChangeDetection()] }).compileComponents();
    fixture = TestBed.createComponent(Host);
    host = fixture.componentInstance;
    await fixture.whenStable();
  });
  afterEach(() => fixture.destroy());

  it('shows the current value and no list until it is used', () => {
    expect(input().value).toBe('UTC');
    expect(rows()).toHaveLength(0);
  });

  it('filters by case-insensitive substring of the label as the user types', async () => {
    await type('eUR');
    expect(labels()).toEqual(['Europe/Berlin', 'Europe/Paris']);
    await type('zzz');
    expect(rows()).toHaveLength(0);
  });

  it('shows every option on open, whatever the current text', async () => {
    input().focus();
    await key('ArrowDown');
    expect(labels()).toEqual(OPTIONS.map((o) => o.label));
    expect(rows()[0].textContent).toContain('GMT-6');
  });

  it('highlights the first match, moves the highlight with the arrow keys, and picks it on Enter', async () => {
    await type('europe');
    const active = () => rows().findIndex((r) => r.getAttribute('data-active') === 'true');
    expect(active()).toBe(0);
    await key('ArrowDown');
    expect(active()).toBe(1);
    await key('Enter');

    expect(host.draft()).toBe('Europe/Paris');
    expect(input().value).toBe('Europe/Paris');
    expect(rows()).toHaveLength(0);
  });

  it('closes on Escape and restores the current value text', async () => {
    await type('eur');
    expect(rows().length).toBeGreaterThan(0);
    await key('Escape');

    expect(rows()).toHaveLength(0);
    expect(input().value).toBe('UTC');
    expect(host.draft()).toBe('UTC');
  });

  it('restores the current text when focus leaves after an edit that picked nothing', async () => {
    await type('zzz');
    input().blur();
    await new Promise((r) => setTimeout(r));
    await fixture.whenStable();

    expect(input().value).toBe('UTC');
    expect(host.draft()).toBe('UTC');
  });

  it('picks a clicked row', async () => {
    await type('chi');
    rows()[0].click();
    await fixture.whenStable();

    expect(host.draft()).toBe('America/Chicago');
    expect(input().value).toBe('America/Chicago');
  });

  it('closes on a re-pick of the current row', async () => {
    await type('utc');
    rows()[0].click();
    await fixture.whenStable();

    expect(host.draft()).toBe('UTC');
    expect(rows()).toHaveLength(0);
  });

  it('lists a stored value that has no option first, marked not in list, and keeps it', async () => {
    host.draft.set('Mars/Olympus');
    await fixture.whenStable();
    expect(input().value).toBe('Mars/Olympus');

    input().focus();
    await key('ArrowDown');
    expect(labels()[0]).toBe('Mars/Olympus');
    expect(rows()[0].textContent).toContain(NOT_IN_LIST);

    await key('Escape');
    expect(host.draft()).toBe('Mars/Olympus');
  });

  it('offers the none option, picking which clears the value', async () => {
    host.none.set('None');
    await fixture.whenStable();
    input().focus();
    await key('ArrowDown');
    expect(labels()[0]).toBe('None');

    rows()[0].click();
    await fixture.whenStable();
    expect(host.draft()).toBe('');
    expect(input().value).toBe('None');
  });
});
