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
    expect(root.querySelector('[role="listbox"]')?.getAttribute('aria-label')).toBe('Project color');
    const chips = root.querySelectorAll<HTMLButtonElement>('[role="option"]');
    expect(chips.length).toBe(Object.values(ProjectColor).length + 1);
    expect(chips[chips.length - 1].querySelector<HTMLElement>('[data-testid]')?.dataset['testid']).toBe('project-color-custom');
    expect(root.querySelector('[data-testid="project-color-custom"]')?.closest('[role=option]')?.getAttribute('aria-selected')).toBe('false');
    expect(root.querySelector('[data-testid="project-color-custom-input"]')).toBeNull();
    expect(root.querySelector('[data-testid="project-color-teal"]')?.closest('[role=option]')?.getAttribute('aria-selected')).toBe('true');
    expect(root.querySelector('[data-testid="project-color-pink"]')?.closest('[role=option]')?.getAttribute('aria-selected')).toBe('false');
  });

  it('emits the chosen color', async () => {
    const fixture = await mount();
    const chosen: string[] = [];
    fixture.componentInstance.choose.subscribe((color) => chosen.push(color));
    (fixture.nativeElement as HTMLElement).querySelector<HTMLButtonElement>('[data-testid="project-color-pink"]')!.click();
    expect(chosen).toEqual(['pink']);
  });

  it('emits once when a pointer focuses and picks a palette color after a custom color', async () => {
    const fixture = await mount(false, '#ff8800');
    const chosen: string[] = [];
    fixture.componentInstance.choose.subscribe((color) => chosen.push(color));
    const option = (fixture.nativeElement as HTMLElement).querySelector('[data-testid="project-color-pink"]')!.closest<HTMLElement>('[role=option]')!;
    option.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }));
    option.focus();
    option.dispatchEvent(new MouseEvent('pointerup', { bubbles: true }));
    option.click();
    expect(chosen).toEqual(['pink']);
  });

  it('selects Custom and seeds the input with the color when the current color is a hex', async () => {
    const fixture = await mount(false, '#ff8800');
    const root: HTMLElement = fixture.nativeElement;
    expect(root.querySelector('[data-testid="project-color-custom"]')?.closest('[role=option]')?.getAttribute('aria-selected')).toBe('true');
    expect(root.querySelector('[data-testid="project-color-teal"]')?.closest('[role=option]')?.getAttribute('aria-selected')).toBe('false');
    expect(root.querySelector<HTMLInputElement>('[data-testid="project-color-custom-input"]')?.value).toBe('#ff8800');
  });

  it('reveals the input on Custom over a palette color, and emits a committed hex through choose', async () => {
    const fixture = await mount();
    const root: HTMLElement = fixture.nativeElement;
    const chosen: string[] = [];
    fixture.componentInstance.choose.subscribe((color) => chosen.push(color));
    root.querySelector<HTMLButtonElement>('[data-testid="project-color-custom"]')!.click();
    await fixture.whenStable();
    expect(chosen).toEqual([]);
    expect(root.querySelector('[data-testid="project-color-custom"]')?.closest('[role=option]')?.getAttribute('aria-selected')).toBe('true');
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

  it('opens Custom through keyboard navigation without emitting a color', async () => {
    const palette = Object.values(ProjectColor);
    const fixture = await mount(false, palette[palette.length - 1]);
    const root: HTMLElement = fixture.nativeElement;
    const chosen: string[] = [];
    fixture.componentInstance.choose.subscribe((color) => chosen.push(color));
    const options = root.querySelectorAll<HTMLElement>('[role=option]');
    const lastPalette = options[options.length - 2];
    lastPalette.focus();
    lastPalette.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    await fixture.whenStable();
    expect(chosen).toEqual([]);
    expect(options[options.length - 1].getAttribute('aria-selected')).toBe('true');
    expect(root.querySelector('[data-testid="project-color-custom-input"]')).not.toBeNull();
  });

  it('disables every chip while an edit is pending', async () => {
    const root: HTMLElement = (await mount(true)).nativeElement;
    const chips = [...root.querySelectorAll<HTMLButtonElement>('[role="option"]')];
    expect(chips.every((chip) => chip.getAttribute('aria-disabled') === 'true')).toBe(true);
  });

  it('disables the custom color input along with the chips while an edit is pending', async () => {
    const root: HTMLElement = (await mount(true, '#ff8800')).nativeElement;
    expect(root.querySelector<HTMLInputElement>('[data-testid="project-color-custom-input"]')?.disabled).toBe(true);
    expect(root.querySelector('[data-testid="project-color-pink"]')?.closest('[role=option]')?.getAttribute('aria-disabled')).toBe('true');
  });
});
