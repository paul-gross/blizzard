import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import type { hubApi } from 'fleet';
import { page } from 'vitest/browser';

import { ChunkGeneralTab } from './chunk-general-tab';

/**
 * The chunk detail page's General tab two-column arrangement half of
 * `web:shell-sweep` (`blizzard-context:/verification/blizzard.md`
 * bzh:web-shell-sweep, blizzard#203) — a real, headless-Chromium proof of the
 * `@media (min-width: 720px)` grid `chunk-general-tab.ts` declares: jsdom
 * parses that query without ever evaluating it, so `web:unit-test` cannot see
 * the two-column split or its collapse.
 *
 * Excluded from the default `ng test hub` run (`angular.json`'s
 * `test.exclude`) because it needs `--browsers=ChromiumHeadless`, not jsdom —
 * run it via `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
const DETAIL: hubApi.ChunkDetail = {
  chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
  graph_id: 'gr_1',
  graph_name: 'default',
  current_node_id: 'nd_review',
  current_node_name: 'review',
  latest_epoch: 2,
  status: 'running',
  work_refs: [{ source: 'blizzard', ref: '26', web_url: null }],
  history: [
    {
      choice_name: null,
      epoch: 1,
      from_node_id: null,
      to_node_id: 'nd_build',
      to_node_name: 'build',
      recorded_at: '2026-07-16T11:00:00.000Z',
    },
  ],
  artifacts: [],
};

/**
 * The same tab with an open ask, shaped the way agents actually write them: paragraph
 * breaks, a numbered list, and a bare unbroken repo path far wider than a 320px phone.
 * The dock preserves the newlines (`chunk-awaiting-human.ts` `.ask-q`), which is exactly
 * what stops lines breaking on spaces alone — so the long token has to be proven to break
 * rather than push the dock sideways, a claim only a real layout engine can make.
 */
const ASK_DETAIL: hubApi.ChunkDetail = {
  ...DETAIL,
  status: 'waiting_on_human',
  questions: [
    {
      question_id: 'qn_long',
      chunk_id: DETAIL.chunk_id,
      question:
        'Issue #214 does not reproduce under any condition I can test. Details:\n\n' +
        '1. Code trace: the mutation already invalidates both query keys.\n' +
        '2. Live browser test: the board updated within 500ms.\n\n' +
        'The failing path is web/projects/fleet/src/lib/chunk-detail/chunk-awaiting-human.spec.ts\n\n' +
        'How should I proceed?',
      options: [],
      epoch: 2,
      runner_id: 'rn_01',
      asked_at: '2026-07-16T11:30:00.000Z',
      answered: false,
    },
  ],
};

/**
 * A `needs_human` chunk carrying both the runner-composed wrapped takeover command
 * (blizzard#251) and its raw `cd <workdir> && <harness resume>` fallback — realistically
 * long strings shaped the way they actually arrive: an absolute runtime dir plus a
 * `blizzard runner takeover` invocation, and an absolute worktree path plus a
 * `claude --resume <uuid>` invocation. The fallback renders inside a collapsed
 * `<details>` (`chunk-takeover.ts`), so the sweep below proves the dock does not
 * overflow with it collapsed *or* expanded.
 */
const NEEDS_HUMAN_DETAIL: hubApi.ChunkDetail = {
  ...DETAIL,
  status: 'needs_human',
  escalation: {
    epoch: 2,
    takeover_command:
      'cd /home/blizzard/runner/work/ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9-nd_review-2 && ' +
      'claude --resume 5f9c2e3a-4b7d-4a1e-9c3f-8d2b6a1e7f4c',
    wrapped_takeover_command:
      'blizzard runner takeover ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9 --dir /home/blizzard/runner/data/runtime',
  },
};

async function render(detail: hubApi.ChunkDetail = DETAIL) {
  await TestBed.configureTestingModule({
    imports: [ChunkGeneralTab],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(ChunkGeneralTab);
  fixture.componentRef.setInput('detail', detail);
  fixture.componentRef.setInput('workItems', { status: 'success', items: [] });
  await fixture.whenStable();
  return fixture;
}

describe('chunk page General tab layout shell sweep (web:shell-sweep, blizzard#203)', () => {
  it('stacks work item, issues and node history at narrow widths with no horizontal overflow', async () => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);

    const fixture = await render();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      for (const width of [390, 320]) {
        await page.viewport(width, 800);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        const label = `width ${width}`;
        const workItem = root.querySelector<HTMLElement>('[data-testid="section-work-item"]');
        const issues = root.querySelector<HTMLElement>('[data-testid="section-issues"]');
        const history = root.querySelector<HTMLElement>('[data-testid="section-node-history"]');
        expect(workItem, `${label}: no work-item panel`).not.toBeNull();
        expect(issues, `${label}: no issues panel`).not.toBeNull();
        expect(history, `${label}: no node-history panel`).not.toBeNull();

        const rects = [workItem!, issues!, history!].map((el) => el.getBoundingClientRect());
        const tops = rects.map((r) => r.top);
        expect(new Set(tops).size, `${label}: panels did not stack — tops were ${tops.join(', ')}`).toBe(3);
        const lefts = new Set(rects.map((r) => r.left));
        expect(lefts.size, `${label}: stacked panels are not left-aligned — lefts were ${[...lefts].join(', ')}`).toBe(1);

        const general = root.querySelector<HTMLElement>('[data-testid="chunk-general-tab"]')!;
        expect(
          general.scrollWidth,
          `${label}: General tab overflows horizontally (${general.scrollWidth} > ${general.clientWidth})`,
        ).toBeLessThanOrEqual(general.clientWidth);
      }
    } finally {
      root.remove();
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
    }

    expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
  });

  it('sits node history beside a shared work-item/issues left column at 1024px', async () => {
    const pageErrors: string[] = [];
    const onError = (e: ErrorEvent) => pageErrors.push(e.message);
    const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);

    const fixture = await render();
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(1024, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const workItem = root.querySelector<HTMLElement>('[data-testid="section-work-item"]')!;
      const issues = root.querySelector<HTMLElement>('[data-testid="section-issues"]')!;
      const history = root.querySelector<HTMLElement>('[data-testid="section-node-history"]')!;

      const workItemRect = workItem.getBoundingClientRect();
      const issuesRect = issues.getBoundingClientRect();
      const historyRect = history.getBoundingClientRect();

      expect(
        historyRect.left,
        `node history's left (${historyRect.left}) is not beside the work-item column (right edge ${workItemRect.right})`,
      ).toBeGreaterThanOrEqual(workItemRect.right);
      expect(
        workItemRect.top,
        `work item and issues share a top (${workItemRect.top}) — they are not stacked in the left column`,
      ).not.toBe(issuesRect.top);
      expect(workItemRect.left, 'work item and issues are not left-aligned with each other').toBe(issuesRect.left);
    } finally {
      root.remove();
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
    }

    expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
  });

  it("renders an agent's multi-paragraph ask on its own lines without overflowing a phone", async () => {
    const fixture = await render(ASK_DETAIL);
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      for (const width of [390, 320]) {
        await page.viewport(width, 800);
        await new Promise((resolve) => requestAnimationFrame(resolve));

        const label = `width ${width}`;
        const ask = root.querySelector<HTMLElement>('[data-testid="question-text"]');
        expect(ask, `${label}: no ask text in the DOM`).not.toBeNull();

        // Height alone is weak — this question wraps to several lines even collapsed. The
        // claim is that the *breaks* survive, so measure the same element both ways: only
        // preserved newlines make it taller than its own collapsed rendering. Toggling in
        // place keeps width, font, and box identical, so the height delta is the newlines
        // and nothing else.
        const preserved = ask!.offsetHeight;
        ask!.style.whiteSpace = 'normal';
        await new Promise((resolve) => requestAnimationFrame(resolve));
        const collapsed = ask!.offsetHeight;
        ask!.style.whiteSpace = '';
        await new Promise((resolve) => requestAnimationFrame(resolve));
        expect(
          preserved,
          `${label}: ask rendered no taller than its own collapsed rendering (${preserved} vs ${collapsed}) — newlines were not preserved`,
        ).toBeGreaterThan(collapsed);

        // The long unbroken path must break rather than push the dock sideways.
        expect(
          ask!.scrollWidth,
          `${label}: ask text overflows horizontally (${ask!.scrollWidth} > ${ask!.clientWidth})`,
        ).toBeLessThanOrEqual(ask!.clientWidth);
        const general = root.querySelector<HTMLElement>('[data-testid="chunk-general-tab"]')!;
        expect(
          general.scrollWidth,
          `${label}: General tab overflows horizontally (${general.scrollWidth} > ${general.clientWidth})`,
        ).toBeLessThanOrEqual(general.clientWidth);
      }
    } finally {
      root.remove();
    }
  });

  it('keeps a long wrapped takeover command and its raw fallback scrollable, not clipped, at 320px (blizzard#251)', async () => {
    const fixture = await render(NEEDS_HUMAN_DETAIL);
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await fixture.whenStable();

    try {
      await page.viewport(320, 800);
      await new Promise((resolve) => requestAnimationFrame(resolve));

      // The no-overflow half is structural here: `fleet-kit-panel`'s body clips
      // horizontally (`kit-panel.ts` `overflow-x: hidden`), so the General tab
      // cannot widen no matter what the takeover panel does. That same clip is
      // exactly why the load-bearing claim is the opposite one — a command wider
      // than the phone must be REACHABLE by scrolling its own box, or the clip
      // silently amputates the tail of the one string the operator must paste
      // whole. `scrollLeft` round-trips only on a genuine scroll container:
      // losing `overflow-x: auto` on either `.cmd` leaves it stuck at 0.
      const general = root.querySelector<HTMLElement>('[data-testid="chunk-general-tab"]')!;
      expect(
        general.scrollWidth,
        `General tab overflows horizontally (${general.scrollWidth} > ${general.clientWidth})`,
      ).toBeLessThanOrEqual(general.clientWidth);

      const primary = root.querySelector<HTMLElement>('[data-testid="takeover-command"]')!;
      expect(
        primary.scrollWidth,
        'fixture defect: wrapped command fits 320px, so scrollability is unprovable',
      ).toBeGreaterThan(primary.clientWidth);
      primary.scrollLeft = 99999;
      expect(primary.scrollLeft, 'wrapped command is clipped, not scrollable').toBeGreaterThan(0);

      const fallback = root.querySelector<HTMLDetailsElement>('[data-testid="takeover-command-raw-fallback"]');
      expect(fallback, 'no raw fallback disclosure in the DOM').not.toBeNull();
      fallback!.open = true;
      await new Promise((resolve) => requestAnimationFrame(resolve));

      const raw = fallback!.querySelector<HTMLElement>('.cmd')!;
      expect(
        raw.scrollWidth,
        'fixture defect: raw fallback fits 320px, so scrollability is unprovable',
      ).toBeGreaterThan(raw.clientWidth);
      raw.scrollLeft = 99999;
      expect(raw.scrollLeft, 'raw fallback is clipped, not scrollable').toBeGreaterThan(0);

      expect(
        general.scrollWidth,
        `expanded: General tab overflows horizontally (${general.scrollWidth} > ${general.clientWidth})`,
      ).toBeLessThanOrEqual(general.clientWidth);
    } finally {
      root.remove();
    }
  });
});
