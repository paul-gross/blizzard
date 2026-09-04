import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page } from 'vitest/browser';

import type { ArtifactView } from '../api/hub';
import { ChunkArtifactBody } from './chunk-artifact-body';

/**
 * The two structured artifact readings — `FindingDelta` and `FindingSurvey` — inside a
 * height-capped page, a real layout claim jsdom cannot make. `chunk-artifact-body.ts`'s
 * own class doc promises that "in a height-capped page it gives the body the scroll
 * region a long findings text — verbatim or structured — needs", and that promise rests
 * entirely on a flex/`min-height: 0` chain four components deep: the body's host, the
 * delta/survey host, its `.fd`/`.fs` column, the disclosure shell, and finally
 * `.rd-body`'s own `overflow: auto`. jsdom parses every one of those rules and lays out
 * none of them, so `web:unit-test` cannot tell a resolved chain from an inert one.
 *
 * Both shapes are swept, never just one: they render in the same slot for the same
 * reason, and a sizing rule naming only the delta is exactly the defect this exists to
 * catch.
 *
 * Excluded from the default `ng test fleet` run (`angular.json`'s `test.exclude`) because
 * it needs `--browsers=ChromiumHeadless`, not jsdom — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */

/** Enough entries that the structured column cannot possibly fit the capped height
 * below — the load-bearing half of the fixture, since a body short enough never to need
 * a scroll region would pass whether or not the chain resolves. */
const ENTRY_COUNT = 60;

function deltaContent(): string {
  return JSON.stringify({
    scope: 'web',
    revisions: { blizzard: 'a'.repeat(40) },
    measurement: 'Swept every module under web/projects for narrated change history.',
    findings: Array.from({ length: ENTRY_COUNT }, (_, i) => ({
      op: 'add',
      ref: `c${i}`,
      class: 'stale-docstring',
      locus: `web/projects/fleet/src/lib/module-${i}.ts:${i + 1}`,
      summary: `Module ${i} narrates its own change history in prose that runs long enough to wrap.`,
      introduced: null,
    })),
  });
}

function surveyContent(): string {
  return JSON.stringify({
    scope: 'web',
    revisions: { blizzard: 'b'.repeat(40) },
    measurement: 'Swept every module under web/projects for narrated change history.',
    candidates: Array.from({ length: ENTRY_COUNT }, (_, i) => ({
      ref: `c${i}`,
      class: 'stale-docstring',
      locus: `web/projects/fleet/src/lib/module-${i}.ts:${i + 1}`,
      summary: `Module ${i} narrates its own change history in prose that runs long enough to wrap.`,
      introduced: null,
    })),
  });
}

function artifact(content: string): ArtifactView {
  return {
    key: 'reconcile.finding-set.1',
    kind: 'asset',
    name: 'finding-set',
    node_id: 'nd_reconcile',
    node_name: 'reconcile',
    epoch: 1,
    content,
    recorded_at: '2026-08-09T00:00:00.000Z',
  } as ArtifactView;
}

const CAP = 420;

/** Stands in for the real height-capped, flex-column ancestor a page gives this body —
 * `chunk-artifacts-tab-layout.shell-sweep.spec.ts`'s own `mountInAppShell` shape, minus
 * the router, since this sweep needs the box and not the route. */
async function mountCapped(content: string): Promise<HTMLElement> {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [ChunkArtifactBody],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(ChunkArtifactBody);
  fixture.componentRef.setInput('artifact', artifact(content));
  fixture.componentRef.setInput('testid', 'artifact');
  await fixture.whenStable();

  const shell = document.createElement('div');
  shell.style.cssText = `display: flex; flex-direction: column; height: ${CAP}px; min-height: 0; overflow: hidden;`;
  const root = fixture.nativeElement as HTMLElement;
  root.style.cssText = 'flex: 1; min-height: 0;';
  shell.appendChild(root);
  document.body.appendChild(shell);
  await page.viewport(800, 700);
  await new Promise((resolve) => requestAnimationFrame(resolve));
  return shell;
}

describe('structured artifact body scroll region shell sweep (web:shell-sweep)', () => {
  for (const [shape, content] of [
    ['delta', deltaContent()],
    ['survey', surveyContent()],
  ] as const) {
    it(`gives the ${shape} reading a scroll region bounded by the capped page, not the content`, async () => {
      const shell = await mountCapped(content);
      try {
        const body = shell.querySelector<HTMLElement>(`[data-testid="artifact-${shape}"]`);
        expect(body, `no artifact-${shape} in the DOM — the shape did not parse`).not.toBeNull();

        const scroller = shell.querySelector<HTMLElement>('.rd-body');
        expect(scroller, 'no .rd-body — the disclosure shell did not render its summary branch').not.toBeNull();

        // The fixture only proves anything if the content genuinely exceeds the cap.
        expect(
          scroller!.scrollHeight,
          `${shape}: fixture defect — the content fits the cap, so no scroll claim is under test`,
        ).toBeGreaterThan(CAP);

        // The regression's own signature: with the chain inert, the host sizes to its
        // content instead of the cap, so nothing scrolls and the column overflows the
        // page it sits in.
        expect(
          scroller!.clientHeight,
          `${shape}: .rd-body grew to its content (${scroller!.clientHeight}px) instead of staying within the ${CAP}px cap`,
        ).toBeLessThanOrEqual(CAP);
        expect(
          scroller!.scrollHeight,
          `${shape}: .rd-body has no overflow to scroll — its box is not bounded by the page`,
        ).toBeGreaterThan(scroller!.clientHeight);

        const shellRect = shell.getBoundingClientRect();
        const bodyRect = body!.getBoundingClientRect();
        expect(
          bodyRect.bottom,
          `${shape}: the structured column's bottom (${bodyRect.bottom}) overflows the capped page's own (${shellRect.bottom})`,
        ).toBeLessThanOrEqual(shellRect.bottom + 0.5);
      } finally {
        shell.remove();
      }
    });
  }
});
