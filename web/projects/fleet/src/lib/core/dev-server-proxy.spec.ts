import { readFileSync } from 'node:fs';

/*
 * The dev server reaches its daemon only by being wired to a proxy config in
 * `angular.json`. Drop that entry and nothing fails that anyone would notice: the app
 * builds, lints, and unit-tests green, and only a human running `ng serve` finds every
 * `/api` read 404ing against the dev server instead of the hub. That silent-regression
 * shape is why the wiring is asserted here rather than left to the next person to
 * rediscover in a browser.
 *
 * Read from disk (relative to the workspace root the runner runs from) rather than
 * imported — these are CJS config files the build pipeline does not bundle.
 */
const angularJson = JSON.parse(readFileSync('angular.json', 'utf8')) as {
  projects: Record<string, { architect?: { serve?: { options?: { proxyConfig?: string } } } }>;
};

const APPS = [
  { app: 'hub', config: 'proxy.conf.hub.js', portVar: 'BZ_HUB_PORT' },
  { app: 'runner', config: 'proxy.conf.runner.js', portVar: 'BZ_RUNNER_PORT' },
];

describe('dev-server proxy wiring', () => {
  for (const { app, config, portVar } of APPS) {
    it(`points the ${app} dev server at ${config}`, () => {
      expect(angularJson.projects[app].architect?.serve?.options?.proxyConfig).toBe(config);
    });

    it(`${config} reads its port from ${portVar} rather than pinning one`, () => {
      // Each feature env binds its own daemon port from the winter band, so a literal
      // here would be correct in exactly one env and silently wrong in every other.
      const source = readFileSync(config, 'utf8');
      expect(source).toContain(`process.env.${portVar}`);
      expect(source).toMatch(/target:\s*`http:\/\/127\.0\.0\.1:\$\{port\}`/);
    });
  }

  it('gives each app its own config — both request /api but must reach different daemons', () => {
    const configs = APPS.map((a) => angularJson.projects[a.app].architect?.serve?.options?.proxyConfig);
    expect(new Set(configs).size).toBe(APPS.length);
  });
});
