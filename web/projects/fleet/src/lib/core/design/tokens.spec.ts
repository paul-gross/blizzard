import { readFileSync } from 'node:fs';

// The design-token stylesheet is the single owner of the mission-control color
// layer. This test asserts the stylesheet is present and carries the load-bearing
// tokens with their exact values, so an accidental edit or drop is caught. Read from disk
// (relative to the workspace root the runner runs from) rather than imported —
// the build pipeline turns a `.css` import into a lazy chunk, not a string.
const TOKENS_PATH = 'projects/fleet/src/lib/core/design/tokens.css';
const tokensCss = readFileSync(TOKENS_PATH, 'utf8');

describe('design tokens', () => {
  it('defines a :root custom-property block', () => {
    expect(tokensCss).toContain(':root');
    expect(tokensCss).toContain('--mono:');
  });

  it('carries the mission-control palette verbatim', () => {
    const expected: Record<string, string> = {
      '--bg': '#060a12',
      '--panel': '#0b1120',
      '--bezel': '#1d2b44',
      '--amber': '#f2b25c',
      '--amber-hi': '#ffcf8a',
      '--cyan': '#5cd1e5',
      '--red': '#f05c6c',
      '--green': '#4fc57e',
      '--label': '#98a8bd',
      '--label-dim': '#7f91a9',
      '--text': '#b8c6d8',
    };
    for (const [name, value] of Object.entries(expected)) {
      expect(tokensCss).toContain(`${name}: ${value}`);
    }
  });

  it('keeps the muted text tokens legible and ordered', () => {
    const token = (name: string): string =>
      new RegExp(`${name}: (#[0-9a-f]{6})`).exec(tokensCss)![1];
    const luminance = (hex: string): number => {
      const [r, g, b] = [1, 3, 5].map((i) => {
        const c = parseInt(hex.slice(i, i + 2), 16) / 255;
        return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    const contrast = (a: string, b: string): number => {
      const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
      return (hi + 0.05) / (lo + 0.05);
    };
    for (const name of ['--label', '--label-dim']) {
      for (const bg of ['--bg', '--panel', '--panel-deep']) {
        expect(contrast(token(name), token(bg))).toBeGreaterThanOrEqual(4.5);
      }
      expect(contrast(token(name), token('--panel-hi'))).toBeGreaterThanOrEqual(3);
    }
    const order = ['--label-dim', '--label', '--text', '--snow'].map((n) => luminance(token(n)));
    expect(order).toEqual([...order].sort((a, b) => a - b));
    expect(new Set(order).size).toBe(order.length);
  });

  it('carries the overlay opacity scale (issue #78) verbatim', () => {
    const expected: Record<string, string> = {
      '--overlay-20': 'rgba(0, 0, 0, 0.2)',
      '--overlay-25': 'rgba(0, 0, 0, 0.25)',
      '--overlay-30': 'rgba(0, 0, 0, 0.3)',
      '--overlay-35': 'rgba(0, 0, 0, 0.35)',
      '--overlay-40': 'rgba(0, 0, 0, 0.4)',
      '--overlay-90': 'rgba(0, 0, 0, 0.9)',
    };
    for (const [name, value] of Object.entries(expected)) {
      expect(tokensCss).toContain(`${name}: ${value}`);
    }
  });
});
