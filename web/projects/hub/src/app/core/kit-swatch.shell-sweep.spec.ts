import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitSwatch, ProjectColor } from 'fleet';

const COLORS: readonly string[] = Object.values(ProjectColor);

/** Custom colors: a hue spread of dark inputs, including black and a dark blue, and one light one. */
const CUSTOM: readonly string[] = ['#000000', '#1a1f4d', '#0000ff', '#3b0764', '#7f1d1d', '#064e3b', '#ff8800'];

/** The WCAG contrast ratio of two `rgb(r, g, b)` computed colors. */
function contrast(a: string, b: string): number {
  const luminance = (css: string): number => {
    const [r, g, b] = (css.match(/[\d.]+/g) ?? []).slice(0, 3).map((part) => {
      const channel = Number(part) / 255;
      return channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

@Component({
  imports: [KitSwatch],
  template: `
    @for (color of colors; track color) {
      <fleet-kit-swatch [color]="color" [attr.data-color]="color" />
    }
    @for (color of custom; track color) {
      <fleet-kit-swatch [color]="color" [attr.data-custom]="color" />
    }
  `,
})
class SweepHost {
  readonly colors = COLORS;
  readonly custom = CUSTOM;
}

/**
 * `KitSwatch`'s half of `web:shell-sweep`: every name in the generated `ProjectColor` enum resolves, through
 * `tokens.css`, to a real and distinct background that holds 3:1 against the resting and selected surfaces — the guard
 * against the enum and the tokens drifting apart. A custom `#rrggbb` holds the same bar after the kit's legibility
 * adjustment, however dark it was typed.
 */
describe('KitSwatch shell sweep (web:shell-sweep)', () => {
  it('resolves every palette name to a distinct color that stands out from both surfaces', async () => {
    await TestBed.configureTestingModule({
      imports: [SweepHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(SweepHost);
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      const probe = document.createElement('div');
      document.body.appendChild(probe);
      const surface = (token: string): string => {
        probe.style.background = `var(${token})`;
        return getComputedStyle(probe).backgroundColor;
      };
      const [resting, selected] = [surface('--surface-resting'), surface('--surface-selected')];
      probe.remove();

      const seen = new Map<string, string>();
      for (const color of COLORS) {
        const swatch = root.querySelector<HTMLElement>(`[data-color="${color}"] .swatch`)!;
        const background = getComputedStyle(swatch).backgroundColor;
        expect(background, `${color}: no resolved background`).not.toBe('');
        expect(background, `${color}: transparent`).not.toBe('rgba(0, 0, 0, 0)');
        expect(seen.get(background), `${color} repeats ${seen.get(background)}`).toBeUndefined();
        seen.set(background, color);
        expect(contrast(background, resting), `${color} against the resting surface`).toBeGreaterThanOrEqual(3);
        expect(contrast(background, selected), `${color} against the selected surface`).toBeGreaterThanOrEqual(3);
      }
      expect(seen.size).toBe(COLORS.length);
    } finally {
      root.remove();
      fixture.destroy();
    }
  });

  it('draws every custom hex, dark ones included, at 3:1 against both surfaces', async () => {
    await TestBed.configureTestingModule({
      imports: [SweepHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(SweepHost);
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      const probe = document.createElement('div');
      document.body.appendChild(probe);
      const surface = (token: string): string => {
        probe.style.background = `var(${token})`;
        return getComputedStyle(probe).backgroundColor;
      };
      const [resting, selected] = [surface('--surface-resting'), surface('--surface-selected')];
      probe.remove();

      for (const color of CUSTOM) {
        const swatch = root.querySelector<HTMLElement>(`[data-custom="${color}"] .swatch`)!;
        const background = getComputedStyle(swatch).backgroundColor;
        expect(background, `${color}: not a resolved rgb()`).toMatch(/^rgb\(/);
        expect(contrast(background, resting), `${color} against the resting surface`).toBeGreaterThanOrEqual(3);
        expect(contrast(background, selected), `${color} against the selected surface`).toBeGreaterThanOrEqual(3);
      }
    } finally {
      root.remove();
      fixture.destroy();
    }
  });
});
