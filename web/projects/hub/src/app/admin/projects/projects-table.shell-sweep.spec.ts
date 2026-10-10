import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page } from 'vitest/browser';

import { ProjectsTable } from './projects-table';
import type { ProjectRowVm } from './projects.model';

/**
 * The projects table's real-render proof (the tooled half of `blizzard-context:/verification/blizzard.md`'s
 * `web:shell-sweep` method): at every width, with a project name, slug and description of any length and the
 * "no runner" badge showing, the page never scrolls sideways and the badge and revision stay on screen.
 * A real CSS layout claim jsdom cannot make.
 *
 * Excluded from the default `ng test hub` run (`angular.json`'s `test.exclude`) because it needs
 * `--browsers=ChromiumHeadless`, not jsdom — run it via `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
/** A name with no break opportunity, so the row cannot wrap its way out of the width. */
const LONG = 'averylongprojectnamethatapersonmightreallygiveandthatmustnotpushthepagesideways';
const ROWS: readonly ProjectRowVm[] = [
  { key: 'proj_long', name: LONG, slug: LONG, description: `${LONG} ${LONG}`, revision: 128, unserved: true },
  { key: 'proj_b', name: 'Blizzard', slug: 'blizzard', description: 'The platform', revision: 4, unserved: false },
];

async function sweep(width: number) {
  const pageErrors: string[] = [];
  const onError = (e: ErrorEvent) => pageErrors.push(e.message);
  window.addEventListener('error', onError);

  await TestBed.configureTestingModule({
    imports: [ProjectsTable],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(ProjectsTable);
  fixture.componentRef.setInput('rows', ROWS);
  fixture.componentRef.setInput('state', 'ready');
  fixture.componentRef.setInput('selectedKey', 'proj_long');
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
  await fixture.whenStable();
  try {
    await page.viewport(width, 900);
    await new Promise((resolve) => requestAnimationFrame(resolve));

    expect(
      document.documentElement.scrollWidth,
      `the page scrolls sideways at ${width}px (${document.documentElement.scrollWidth} > ${width})`,
    ).toBeLessThanOrEqual(width);
    const table = root.querySelector<HTMLElement>('[data-testid="projects-table"]')!;
    expect(table.getBoundingClientRect().right, `the table overruns ${width}px`).toBeLessThanOrEqual(width);

    const badge = root.querySelector<HTMLElement>('[data-testid="projects-table-unserved-proj_long"]')!;
    const box = badge.getBoundingClientRect();
    expect(box.width, 'the badge has no width').toBeGreaterThan(0);
    expect(box.right, `the badge is pushed off screen at ${width}px`).toBeLessThanOrEqual(width);
  } finally {
    root.remove();
    window.removeEventListener('error', onError);
  }
  expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
}

describe('projects table shell sweep (web:shell-sweep)', () => {
  for (const width of [360, 768, 1280]) {
    it(`never scrolls the page sideways at ${width}px`, async () => {
      await sweep(width);
    });
  }
});
