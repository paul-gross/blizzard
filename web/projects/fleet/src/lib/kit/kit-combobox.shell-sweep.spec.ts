import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { FormField, form } from '@angular/forms/signals';
import { page, userEvent } from 'vitest/browser';

import { type ComboOption, KitCombobox, NOT_IN_LIST } from './kit-combobox';

const OPTIONS: readonly ComboOption[] = [
  { value: 'America/Chicago', label: 'America/Chicago', meta: 'GMT-6' },
  { value: 'Europe/Berlin', label: 'Europe/Berlin', meta: 'GMT+1' },
  { value: 'Europe/Paris', label: 'Europe/Paris', meta: 'GMT+1' },
  { value: 'UTC', label: 'UTC', meta: 'GMT' },
];

@Component({
  imports: [FormField, KitCombobox],
  template: `
    <fleet-kit-combobox [formField]="f" [options]="options" ariaLabel="Zone" testid="zone" />
    <button data-testid="after" type="button">after</button>
  `,
})
class Host {
  readonly options = OPTIONS;
  readonly draft = signal('UTC');
  readonly f = form(this.draft);
}

const frame = () => new Promise((resolve) => requestAnimationFrame(resolve));

/** The zone picker's keyboard model under real Chromium input: typed text, real key presses, real focus moves. */
describe('KitCombobox keyboard model shell sweep', () => {
  let fixture: ReturnType<typeof TestBed.createComponent<Host>>;
  let root: HTMLElement;

  const box = () => root.querySelector<HTMLInputElement>('[data-testid="zone"]')!;
  const rows = () => [...document.body.querySelectorAll<HTMLElement>('[role="option"]')];
  const labels = () => rows().map((r) => r.querySelector('.label')?.textContent?.trim());
  const active = () => rows().findIndex((r) => r.getAttribute('data-active') === 'true');
  const settle = async () => {
    await fixture.whenStable();
    await frame();
  };

  beforeEach(async () => {
    await page.viewport(390, 800);
    await TestBed.configureTestingModule({ imports: [Host], providers: [provideZonelessChangeDetection()] }).compileComponents();
    fixture = TestBed.createComponent(Host);
    root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();
  });

  afterEach(async () => {
    fixture.destroy();
    root.remove();
    await page.viewport(1280, 800);
  });

  it('filters as the user types, moves the highlight with the arrows, and picks on Enter', async () => {
    await userEvent.click(box());
    await userEvent.clear(box());
    await userEvent.type(box(), 'europe');
    await settle();
    expect(labels()).toEqual(['Europe/Berlin', 'Europe/Paris']);
    expect(active()).toBe(0);

    await userEvent.keyboard('{ArrowDown}');
    await settle();
    expect(active()).toBe(1);

    await userEvent.keyboard('{Enter}');
    await settle();
    expect(fixture.componentInstance.draft()).toBe('Europe/Paris');
    expect(box().value).toBe('Europe/Paris');
    expect(rows()).toHaveLength(0);
  });

  it('closes on Escape without changing the value', async () => {
    await userEvent.click(box());
    await userEvent.clear(box());
    await userEvent.type(box(), 'chi');
    await settle();
    expect(rows().length).toBeGreaterThan(0);

    await userEvent.keyboard('{Escape}');
    await settle();
    expect(rows()).toHaveLength(0);
    expect(box().value).toBe('UTC');
    expect(fixture.componentInstance.draft()).toBe('UTC');
  });

  it('keeps a stored value that no option carries, listed first and marked not in list', async () => {
    fixture.componentInstance.draft.set('Mars/Olympus');
    await settle();
    expect(box().value).toBe('Mars/Olympus');

    await userEvent.click(box());
    await userEvent.keyboard('{ArrowDown}');
    await settle();
    expect(labels()[0]).toBe('Mars/Olympus');
    expect(rows()[0].textContent).toContain(NOT_IN_LIST);

    await userEvent.keyboard('{Escape}');
    await settle();
    expect(fixture.componentInstance.draft()).toBe('Mars/Olympus');
    expect(box().value).toBe('Mars/Olympus');
  });

  it('restores the current text when focus leaves after an edit that matched nothing', async () => {
    await userEvent.click(box());
    await userEvent.clear(box());
    await userEvent.type(box(), 'zzz');
    await settle();
    expect(rows()).toHaveLength(0);

    await userEvent.tab();
    await settle();
    expect(document.activeElement).toBe(root.querySelector('[data-testid="after"]'));
    expect(box().value).toBe('UTC');
    expect(fixture.componentInstance.draft()).toBe('UTC');
  });
});
