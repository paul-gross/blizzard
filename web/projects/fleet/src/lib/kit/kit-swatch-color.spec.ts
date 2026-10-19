import { isHexColor, legibleOnDark } from './kit-swatch-color';

function luminance(hex: string): number {
  const n = parseInt(hex.slice(1), 16);
  const [r, g, b] = [n >> 16, (n >> 8) & 255, n & 255].map((byte) => {
    const c = byte / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** The WCAG contrast of `hex` against `--surface-selected` (`#1a2748`), the lightest dark surface. */
const againstSelected = (hex: string): number =>
  (luminance(hex) + 0.05) / (luminance('#1a2748') + 0.05);

function hue(hex: string): number {
  const n = parseInt(hex.slice(1), 16);
  const [r, g, b] = [n >> 16, (n >> 8) & 255, n & 255];
  return Math.atan2(Math.sqrt(3) * (g - b), 2 * r - g - b);
}

describe('legibleOnDark', () => {
  it('recognises only a #rrggbb as a custom color', () => {
    expect(['#14b8a6', '#FF8800'].every(isHexColor)).toBe(true);
    expect(['teal', '#abc', '14b8a6', '#12345g'].some(isHexColor)).toBe(false);
  });

  it('returns a light color unchanged, lowercased', () => {
    expect(legibleOnDark('#FF8800')).toBe('#ff8800');
    expect(legibleOnDark('#14b8a6')).toBe('#14b8a6');
  });

  it.each(['#000000', '#1a1f4d', '#0000ff', '#3b0764', '#7f1d1d', '#064e3b'])(
    'lifts %s to hold 3:1 against the selected surface',
    (dark) => {
      const lifted = legibleOnDark(dark);
      expect(lifted).toMatch(/^#[0-9a-f]{6}$/);
      expect(lifted).not.toBe(dark);
      expect(againstSelected(lifted)).toBeGreaterThanOrEqual(3);
    },
  );

  it('keeps the hue of a dark color it lifts', () => {
    for (const dark of ['#1a1f4d', '#7f1d1d', '#064e3b']) {
      expect(Math.abs(hue(legibleOnDark(dark)) - hue(dark))).toBeLessThan(0.35);
    }
  });

  it('lifts no further than it must: the same color a hair darker would fall short', () => {
    const lifted = legibleOnDark('#1a1f4d');
    expect(againstSelected(lifted)).toBeLessThan(3.6);
  });
});
