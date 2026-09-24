import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page } from 'vitest/browser';

import type { GraphSessionView } from '../api/hub';
import { GraphSessionTable } from './graph-session-table';

/**
 * The session declaration table's six-column layout (the Harnesses column sits between
 * Model and Effort) — a real layout claim jsdom cannot make. jsdom
 * never lays out `table.sessions`'s cells, so `web:unit-test` can see the harness list's
 * text land in the right `<td>` but not whether the six columns actually sit side by side
 * without a wide harness or model list pushing a later column past the table's own edge.
 *
 * Mounted at 800×600, `graph-detail.shell-sweep.spec.ts`'s own width — the widest this
 * table is ever framed at in the real graph detail page it lives on. Graph detail is not
 * reachable from the hub's mobile bottom tab bar
 * (`projects/hub/src/app/nav/mobile-tab-bar.ts` routes only Board, Asks, Fleet, Events, and
 * Gardening), so `bzh:narrow-viewport-tier-rule` does not bind here.
 *
 * Proven able to fail by widening `table.sessions th`/`td`'s `padding` far enough that the
 * six-column row no longer fits inside the table's own `width: 100%` — the Rotate column's
 * right edge would then overflow the table.
 *
 * Excluded from the default `ng test` run the same way every other `*.shell-sweep.spec.ts`
 * is — run it via `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
 */
const SESSIONS: readonly GraphSessionView[] = [
  {
    name: 'planning',
    model: ['blizzard:advanced'],
    harnesses: ['claude'],
    effort: 'high',
  },
  {
    name: 'code',
    model: ['blizzard:basic', 'gpt-5.3-codex'],
    harnesses: ['claude_code', 'opencode'],
    effort: 'medium',
    compaction_window: '100000',
    rotate: { max_context_tokens: 120000, max_invocations: 30 },
  },
  {
    name: 'gate',
    model: [],
  },
];

async function renderTable(): Promise<HTMLElement> {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [GraphSessionTable],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(GraphSessionTable);
  fixture.componentRef.setInput('sessions', SESSIONS);
  await fixture.whenStable();
  const root = fixture.nativeElement as HTMLElement;
  root.style.cssText = 'display: block; width: 100%;';
  document.body.appendChild(root);
  await page.viewport(800, 600);
  return root;
}

describe('graph session table shell sweep (web:shell-sweep)', () => {
  it('lays out all six columns side by side, with no column overlap or table overflow', async () => {
    const root = await renderTable();
    try {
      const table = root.querySelector<HTMLElement>('table.sessions')!;
      const tableRect = table.getBoundingClientRect();

      const headerCells = Array.from(table.querySelectorAll<HTMLElement>('thead th'));
      expect(headerCells).toHaveLength(6);

      const code = root.querySelector<HTMLElement>('[data-session-name="code"]')!;
      const dataCells = Array.from(code.querySelectorAll<HTMLElement>('td'));
      expect(dataCells).toHaveLength(6);

      for (const cells of [headerCells, dataCells]) {
        for (let i = 0; i < cells.length - 1; i++) {
          const left = cells[i].getBoundingClientRect();
          const right = cells[i + 1].getBoundingClientRect();
          expect(
            right.left,
            `column ${i + 1}'s left edge (${right.left}) does not start at or after column ${i}'s right edge (${left.right})`,
          ).toBeGreaterThanOrEqual(left.right - 0.5);
        }
        const last = cells[cells.length - 1].getBoundingClientRect();
        expect(
          last.right,
          `the last column's right edge (${last.right}) overflows the table's own (${tableRect.right})`,
        ).toBeLessThanOrEqual(tableRect.right + 0.5);
      }

      // The harness list actually rendered in its own column, not collapsed away.
      expect(dataCells[2].textContent?.trim()).toBe('claude code, opencode');
    } finally {
      root.remove();
    }
  });
});
