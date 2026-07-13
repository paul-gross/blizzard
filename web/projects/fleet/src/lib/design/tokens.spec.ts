import { readFileSync } from 'node:fs';

// The design-token stylesheet is the single owner of the mission-control color
// layer (ported verbatim from the discovery mockup's `:root` block, D-097). This
// test asserts the stylesheet is present and carries the load-bearing tokens with
// their exact values, so an accidental edit or drop is caught. Read from disk
// (relative to the workspace root the runner runs from) rather than imported —
// the build pipeline turns a `.css` import into a lazy chunk, not a string.
const TOKENS_PATH = 'projects/fleet/src/lib/design/tokens.css';
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
      '--label': '#5c7089',
      '--text': '#b8c6d8',
    };
    for (const [name, value] of Object.entries(expected)) {
      expect(tokensCss).toContain(`${name}: ${value}`);
    }
  });
});
