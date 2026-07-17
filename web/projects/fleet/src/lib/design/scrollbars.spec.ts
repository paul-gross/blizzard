import { readFileSync } from 'node:fs';

// The scrollbar stylesheet only reaches the apps by being listed in each app's
// build `styles` — a global sheet, since the pseudo-elements must pierce view
// encapsulation. Dropping either the file or a `styles` entry would silently
// restore the OS default scrollbar with nothing else failing, so both halves are
// asserted here. Read from disk (relative to the workspace root the runner runs
// from) rather than imported — the build pipeline turns a `.css` import into a
// lazy chunk, not a string.
const SCROLLBARS_PATH = 'projects/fleet/src/lib/design/scrollbars.css';
const scrollbarsCss = readFileSync(SCROLLBARS_PATH, 'utf8');
const angularJson = JSON.parse(readFileSync('angular.json', 'utf8')) as {
  projects: Record<string, { architect?: { build?: { options?: { styles?: string[] } } } }>;
};

describe('scrollbar styles', () => {
  it('carries the mission-control treatment through the token layer', () => {
    expect(scrollbarsCss).toContain('::-webkit-scrollbar');
    expect(scrollbarsCss).toContain('width: 6px');
    expect(scrollbarsCss).toContain('background: var(--bezel)');
  });

  it('resolves every color through a token, never a hard-coded hex', () => {
    expect(scrollbarsCss).not.toMatch(/#[0-9a-f]{3,8}\b/i);
  });

  for (const app of ['hub', 'runner']) {
    it(`is listed in the ${app} app's global styles`, () => {
      const styles = angularJson.projects[app].architect?.build?.options?.styles ?? [];
      expect(styles).toContain(SCROLLBARS_PATH);
    });
  }
});
