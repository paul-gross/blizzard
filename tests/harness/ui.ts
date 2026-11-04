import { RANK } from './clock-stack';
import { tenancy } from './tenancy';
import viewports from './viewports.json';

/** A `Page` viewport size. */
export interface Viewport {
  width: number;
  height: number;
}

interface UiFixtures {
  /** A desktop-width viewport, the wide end of the narrow-viewport tier rule: `await page.setViewportSize(wideViewport)`. */
  wideViewport: Viewport;
  /** A ~390px phone-width viewport, the narrow end of the tier rule. */
  narrowViewport: Viewport;
}

/** The browser storage key the board reads the tenant it last acted in from (`LAST_TENANT_KEY` in the board's tenant context). */
const LAST_TENANT_KEY = 'blizzard.tenant.last';

/**
 * The browser world: the page's `baseURL` is the test's hub, which serves the built board, the page opens in the test's
 * own tenant rather than at the board's tenant picker, the browser's clock fixed to the stack's instant on a controlled stack, and the two viewport sizes the UI tier reads from the same file.
 */
export const uiWorld = tenancy.extend<UiFixtures>({
  baseURL: async ({ testHub }, use) => {
    await use(testHub.coordinates.url);
  },
  page: async ({ page, tenant, clockStack }, use) => {
    await page.addInitScript(([key, id]) => globalThis.localStorage.setItem(key as string, id as string), [LAST_TENANT_KEY, tenant.id]);
    // On a controlled stack the browser's clock is the stack's last member: fixed at the stack's instant on joining and
    // after every move. `install` is never used, so the page's timers, polling and stream reconnects run as today.
    const membership = await clockStack.join({
      name: 'the browser page',
      rank: RANK.page,
      client: { set: async (to) => { await page.clock.setFixedTime(to); return to; } },
    });
    try {
      await use(page);
    } finally {
      membership.leave();
    }
  },
  wideViewport: async ({}, use) => {
    await use({ ...viewports.wide });
  },
  narrowViewport: async ({}, use) => {
    await use({ ...viewports.narrow });
  },
});
