/**
 * The legibility adjustment a custom project color goes through before `KitSwatch` draws it. A palette name resolves
 * to a token already held to 3:1 against the dark surfaces; a custom `#rrggbb` is anything its author typed, so it is
 * lifted — in OKLCH lightness, keeping its hue and chroma — until it clears the same bar. Pure, so a spec can pin the
 * floor without a render.
 */

/** A custom color's shape: a `#rrggbb` CSS literal, the form the native color picker also yields. */
const HEX = /^#[0-9a-f]{6}$/i;

/**
 * The relative luminance below which a color falls under 3:1 against `--surface-selected` (`#1a2748`, the lightest of
 * the dark surfaces, luminance ≈ 0.0214), the way `tokens.css` states the palette holds: (0.18 + 0.05) / (0.0214 +
 * 0.05) ≈ 3.2. `--surface-resting` is darker, so clearing the one clears both.
 */
const MIN_LUMINANCE = 0.18;

/** Whether `value` is a `#rrggbb` custom color, rather than a palette name. */
export function isHexColor(value: string): boolean {
  return HEX.test(value);
}

/**
 * A project color as a CSS value, unadjusted: the palette name's `--project-<name>` token (the dim label color for an
 * unknown name), or a `#rrggbb` custom color as typed. For a surface tinted by the project rather than a mark drawn
 * on one, where the lift `legibleOnDark` applies would only wash the tint out.
 */
export function projectColorValue(color: string): string {
  return isHexColor(color) ? color.toLowerCase() : `var(--project-${color}, var(--label-dim))`;
}

/** The CSS value a project-tinted card or row paints with ({@link projectColorValue}), or `null` for an untinted one. */
export function projectTint(color: string | null | undefined): string | null {
  return color ? projectColorValue(color) : null;
}

type Rgb = readonly [number, number, number];

const toLinear = (channel: number): number =>
  channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
const fromLinear = (channel: number): number =>
  channel <= 0.0031308 ? channel * 12.92 : 1.055 * channel ** (1 / 2.4) - 0.055;
const clamp01 = (n: number): number => Math.min(1, Math.max(0, n));

function luminance([r, g, b]: Rgb): number {
  return 0.2126 * toLinear(r) + 0.7152 * toLinear(g) + 0.0722 * toLinear(b);
}

function parse(hex: string): Rgb {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

function format([r, g, b]: Rgb): string {
  const byte = (c: number): string =>
    Math.round(clamp01(c) * 255)
      .toString(16)
      .padStart(2, '0');
  return `#${byte(r)}${byte(g)}${byte(b)}`;
}

/** sRGB (0–1) to OKLab `[L, a, b]`. */
function toOklab([r, g, b]: Rgb): [number, number, number] {
  const [lr, lg, lb] = [toLinear(r), toLinear(g), toLinear(b)];
  const l = Math.cbrt(
    0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb,
  );
  const m = Math.cbrt(
    0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb,
  );
  const s = Math.cbrt(
    0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb,
  );
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  ];
}

/** OKLab to sRGB (0–1), each channel clipped into gamut. */
function fromOklab(lightness: number, a: number, b: number): Rgb {
  const l = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (lightness - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const linear = [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ];
  return linear.map((c) => fromLinear(clamp01(c))) as unknown as Rgb;
}

/**
 * `hex` as it should be drawn on the dark surfaces: unchanged (lowercased) when already light enough, else the same
 * hue and chroma at the least OKLCH lightness whose 8-bit sRGB color clears the luminance floor.
 */
export function legibleOnDark(hex: string): string {
  const rgb = parse(hex);
  const rounded = (candidate: Rgb): Rgb => parse(format(candidate));
  if (luminance(rgb) >= MIN_LUMINANCE) {
    return hex.toLowerCase();
  }
  const [floor, a, b] = toOklab(rgb);
  let [low, high] = [floor, 1];
  for (let step = 0; step < 24; step++) {
    const mid = (low + high) / 2;
    if (luminance(rounded(fromOklab(mid, a, b))) >= MIN_LUMINANCE) {
      high = mid;
    } else {
      low = mid;
    }
  }
  return format(fromOklab(high, a, b));
}
