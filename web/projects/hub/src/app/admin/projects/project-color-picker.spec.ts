import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { ProjectColor } from 'fleet';
import { describe, expect, it } from 'vitest';

import { ProjectColorPicker } from './project-color-picker';

describe('ProjectColorPicker', () => {
  async function mount(disabled = false) {
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection()] });
    const fixture = TestBed.createComponent(ProjectColorPicker);
    fixture.componentRef.setInput('colors', Object.values(ProjectColor));
    fixture.componentRef.setInput('current', 'teal');
    fixture.componentRef.setInput('disabled', disabled);
    await fixture.whenStable();
    return fixture;
  }

  it('offers every palette color, the current one pressed', async () => {
    const root: HTMLElement = (await mount()).nativeElement;
    const chips = root.querySelectorAll<HTMLButtonElement>('button[data-testid^="project-color-"]');
    expect(chips.length).toBe(Object.values(ProjectColor).length);
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

  it('disables every chip while an edit is pending', async () => {
    const root: HTMLElement = (await mount(true)).nativeElement;
    const chips = [...root.querySelectorAll<HTMLButtonElement>('button[data-testid^="project-color-"]')];
    expect(chips.every((chip) => chip.disabled)).toBe(true);
  });
});
