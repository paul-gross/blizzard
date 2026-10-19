import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { ProjectColor } from 'fleet';
import { describe, expect, it } from 'vitest';

import { ProjectColorPicker } from './project-color-picker';

describe('ProjectColorPicker', () => {
  async function mount(disabled = false, current = 'teal') {
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection()] });
    const fixture = TestBed.createComponent(ProjectColorPicker);
    fixture.componentRef.setInput('colors', Object.values(ProjectColor));
    fixture.componentRef.setInput('current', current);
    fixture.componentRef.setInput('disabled', disabled);
    await fixture.whenStable();
    return fixture;
  }

  it('offers every palette color, the current one pressed, and a Custom chip after them', async () => {
    const root: HTMLElement = (await mount()).nativeElement;
    const chips = root.querySelectorAll<HTMLButtonElement>('button[data-testid^="project-color-"]');
    expect(chips.length).toBe(Object.values(ProjectColor).length + 1);
    expect(chips[chips.length - 1].dataset['testid']).toBe('project-color-custom');
    expect(root.querySelector('[data-testid="project-color-custom"]')?.getAttribute('aria-pressed')).toBe('false');
    expect(root.querySelector('[data-testid="project-color-custom-input"]')).toBeNull();
    expect(root.querySelector('[data-testid="project-color-teal"]')?.getAttribute('aria-pressed')).toBe('true');
    expect(root.querySelector('[data-testid="project-color-pink"]')?.getAttribute('aria-pressed')).toBe('false');
  });

  it('emits the chosen color', async () => {
    const fixture = await mount();
    const chosen: string[] = [];
    fixture.componentInstance.choose.subscribe((color) => chosen.push(color));
    (fixture.nativeElement as HTMLElement).querySelector<HTMLButtonElement>('[data-testid="project-color-pink"]')!.click();
    expect(chosen).toEqual(['pink']);
  });

  it('selects Custom and seeds the input with the color when the current color is a hex', async () => {
    const fixture = await mount(false, '#ff8800');
    const root: HTMLElement = fixture.nativeElement;
    expect(root.querySelector('[data-testid="project-color-custom"]')?.getAttribute('aria-pressed')).toBe('true');
    expect(root.querySelector('[data-testid="project-color-teal"]')?.getAttribute('aria-pressed')).toBe('false');
    expect(root.querySelector<HTMLInputElement>('[data-testid="project-color-custom-input"]')?.value).toBe('#ff8800');
  });

  it('reveals the input on Custom over a palette color, and emits a committed hex through choose', async () => {
    const fixture = await mount();
    const root: HTMLElement = fixture.nativeElement;
    const chosen: string[] = [];
    fixture.componentInstance.choose.subscribe((color) => chosen.push(color));
    root.querySelector<HTMLButtonElement>('[data-testid="project-color-custom"]')!.click();
    await fixture.whenStable();
    const input = root.querySelector<HTMLInputElement>('[data-testid="project-color-custom-input"]')!;
    expect(input.value).toBe('');
    input.value = '#FF8800';
    input.dispatchEvent(new Event('input'));
    expect(chosen).toEqual(['#ff8800']);
  });

  it('hides the input again when a palette chip is chosen', async () => {
    const fixture = await mount();
    const root: HTMLElement = fixture.nativeElement;
    root.querySelector<HTMLButtonElement>('[data-testid="project-color-custom"]')!.click();
    await fixture.whenStable();
    root.querySelector<HTMLButtonElement>('[data-testid="project-color-pink"]')!.click();
    await fixture.whenStable();
    expect(root.querySelector('[data-testid="project-color-custom-input"]')).toBeNull();
  });

  it('disables every chip while an edit is pending', async () => {
    const root: HTMLElement = (await mount(true)).nativeElement;
    const chips = [...root.querySelectorAll<HTMLButtonElement>('button[data-testid^="project-color-"]')];
    expect(chips.every((chip) => chip.disabled)).toBe(true);
  });
});
