import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page } from 'vitest/browser';

import { KitSelect } from './kit-select';
import type { KitChipOption } from './kit-chips';

@Component({
  imports: [KitSelect],
  template: `
    <fleet-kit-select
      ariaLabel="Class filter"
      [options]="options"
      [selectedValue]="'all'"
      (choose)="chosen = $event"
    />
  `,
})
class SelectHost {
  options: KitChipOption[] = [{ value: 'all', label: 'All classes' }, ...Array.from({ length: 40 }, (_, i) => ({
    value: `class-${i}`,
    label: `Finding class ${i}`,
  }))];
  chosen = '';
}

describe('KitSelect popup shell sweep', () => {
  it('keeps long-list options full height and scrolls to keyboard-focused choices', async () => {
    await page.viewport(390, 800);
    await TestBed.configureTestingModule({
      imports: [SelectHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(SelectHost);
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      root.querySelector<HTMLButtonElement>('button')!.click();
      await fixture.whenStable();
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const popup = document.body.querySelector<HTMLElement>('[role="listbox"]')!;
      const options = [...popup.querySelectorAll<HTMLElement>('[role="option"]')];
      expect(options).toHaveLength(41);
      expect(popup.clientHeight).toBeLessThanOrEqual(320);
      expect(popup.scrollHeight).toBeGreaterThan(popup.clientHeight);
      const heights = options.map((option) => option.getBoundingClientRect().height);
      expect(Math.min(...heights)).toBeGreaterThan(20);
      expect(Math.max(...heights) - Math.min(...heights)).toBeLessThan(1);

      for (let i = 0; i < options.length - 1; i += 1) {
        document.activeElement?.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', keyCode: 40, bubbles: true }));
      }
      await new Promise((resolve) => requestAnimationFrame(resolve));
      expect(document.activeElement).toBe(options.at(-1));
      expect(popup.scrollTop).toBeGreaterThan(0);
      expect(options.at(-1)!.getBoundingClientRect().bottom).toBeLessThanOrEqual(popup.getBoundingClientRect().bottom);
    } finally {
      fixture.destroy();
      root.remove();
      await page.viewport(1280, 800);
    }
  });

  it('leaves a short list at natural height without scrolling', async () => {
    await TestBed.configureTestingModule({
      imports: [SelectHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(SelectHost);
    fixture.componentInstance.options = fixture.componentInstance.options.slice(0, 3);
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();
    try {
      root.querySelector<HTMLButtonElement>('button')!.click();
      await fixture.whenStable();
      const popup = document.body.querySelector<HTMLElement>('[role="listbox"]')!;
      expect(popup.scrollHeight).toBe(popup.clientHeight);
      expect(popup.clientHeight).toBeLessThan(320);
    } finally {
      fixture.destroy();
      root.remove();
    }
  });

  it('renders disabled options distinctly and keeps a keyboard focus target at phone width', async () => {
    await page.viewport(390, 800);
    await TestBed.configureTestingModule({ imports: [SelectHost], providers: [provideZonelessChangeDetection()] }).compileComponents();
    const fixture = TestBed.createComponent(SelectHost);
    fixture.componentInstance.options = [
      { value: 'all', label: 'All classes', disabled: true },
      { value: 'active', label: 'Active classes' },
    ];
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();
    try {
      root.querySelector<HTMLButtonElement>('button')!.click();
      await fixture.whenStable();
      await new Promise((resolve) => requestAnimationFrame(resolve));
      const options = document.body.querySelectorAll<HTMLElement>('[role="option"]');
      expect(options[0].getAttribute('aria-disabled')).toBe('true');
      expect(getComputedStyle(options[0]).opacity).toBe('0.5');
      expect(document.activeElement).toBe(options[1]);
      expect(options[1].getBoundingClientRect().right).toBeLessThanOrEqual(390);
    } finally {
      fixture.destroy();
      root.remove();
      await page.viewport(1280, 800);
    }
  });
});
