import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient } from 'fleet';
import { stubRequestClient } from 'fleet/testing';
import { page } from 'vitest/browser';

import type { MachineChunkRow } from './local-panel';
import { LocalPanelLayout } from './local-panel-layout';

/**
 * The runner local-panel shell's half of `web:shell-sweep`
 * (`blizzard-context:/verification/blizzard.md` bzh:web-shell-sweep) — a
 * real, headless-Chromium proof that this shell's own trailing cluster
 * (pause control + identity + profile menu, `local-panel-layout.ts`) never
 * lets the profile menu drift off-viewport, at every width from a wide
 * monitor down to a phone forced into desktop mode, and at every username
 * length from authless to a 64-character one.
 *
 * This is the shell where issue #163's actual defect lived: the identity
 * block is the header's one *content-dependent* width, so — unlike the hub
 * shell's own sweep — identity length is a real, load-bearing axis here, not
 * a no-op one.
 *
 * Excluded from the default `ng test local-panel` run (`angular.json`'s
 * `test.exclude`) because it needs `--browsers=ChromiumHeadless`, not jsdom —
 * run it via `npm run shell-sweep` (`web/scripts/shell-sweep.js`), which
 * drives both this file and the hub shell's counterpart
 * (`hub/src/app/nav/app-nav-menu.shell-sweep.spec.ts`).
 */
async function render() {
  await TestBed.configureTestingModule({
    imports: [LocalPanelLayout],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      // The detail dock's header links the chunk name to its route now (issue #318).
      provideRouter([]),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(LocalPanelLayout);
  const machineChunk: MachineChunkRow = {
    lease: {
      lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNZPRR',
      chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
      graph_id: 'gr_1',
      node_id: 'nd_build',
      node_name: 'build',
      epoch: 2,
      session_id: 'sess-77',
      pid: 4821,
      environment_id: 'beta',
      workdir: '/ws/beta',
      created_at: '2026-07-16T11:00:00.000Z',
      last_heartbeat_at: '2026-07-16T11:59:26.000Z',
      state: 'running',
      closed_at: null,
      closure_reason: null,
    },
    leases: [],
    status: { label: 'RUNNING', tone: 'running' },
  };
  const defaults = {
    connection: 'ok',
    headerStats: [
      { key: 'envs', label: 'Envs', value: 2, capacity: 4 },
      { key: 'agents', label: 'Agents', value: 1, capacity: 2 },
    ],
    activeLeases: [machineChunk.lease],
    leasesTriadState: 'ready',
    chunksTriadState: 'ready',
    chunksEmptyText: 'NO CHUNKS ON THIS MACHINE',
    machineChunks: [machineChunk],
    showAllChunks: false,
    openAskCount: 0,
    selectedChunkId: null,
    selectedChunkLeases: [],
    selectedStatus: null,
    selectedEscalation: null,
  };
  for (const [key, value] of Object.entries(defaults)) fixture.componentRef.setInput(key, value);
  await fixture.whenStable();
  return fixture;
}

// authless, a short name, a typical one, and long ones straddling the band the
// historical #163 fix's own manual sweep flagged (a name wide enough to push
// the menu off runs to ~1000px at 38 characters — see board-header.ts).
const IDENTITY_LENGTHS = [0, 5, 20, 38, 64];

// 1400 down to 320 — spans a wide monitor to the narrowest common phone,
// straddling every breakpoint this shell and BoardHeader declare (1150px,
// 700px/699px).
const WIDTHS = [1400, 1366, 1280, 1150, 1149, 1100, 1050, 1000, 900, 800, 768, 700, 699, 640, 600, 480, 390, 320];

function usernameOfLength(length: number): string {
  return 'a'.repeat(length);
}

describe('runner local-panel shell sweep (web:shell-sweep, issue #163/#171)', () => {
  for (const length of IDENTITY_LENGTHS) {
    it(`keeps the profile menu on-screen, hit-testable, and overflow-free at every width (username length ${length})`, async () => {
      const pageErrors: string[] = [];
      const onError = (e: ErrorEvent) => pageErrors.push(e.message);
      const onRejection = (e: PromiseRejectionEvent) => pageErrors.push(String(e.reason));
      window.addEventListener('error', onError);
      window.addEventListener('unhandledrejection', onRejection);

      const session =
        length === 0 ? { auth_enabled: false, username: null } : { auth_enabled: true, username: usernameOfLength(length) };
      const stub = stubRequestClient(runnerClient, (method, path) => {
        if (method === 'GET' && path === '/api/auth/session') return session;
        return { items: [] };
      });

      const fixture = await render();
      const root = fixture.nativeElement as HTMLElement;
      document.body.appendChild(root);
      await fixture.whenStable();

      try {
        for (const width of WIDTHS) {
          await page.viewport(width, 800);
          await new Promise((resolve) => requestAnimationFrame(resolve));

          const label = `username length=${length}, width=${width}`;
          const menu = root.querySelector<HTMLElement>('[data-testid="local-panel-menu"]');
          expect(menu, `${label}: no profile menu trigger in the DOM`).not.toBeNull();
          const rect = menu!.getBoundingClientRect();

          expect(rect.width, `${label}: menu has zero width`).toBeGreaterThan(0);
          expect(rect.left, `${label}: menu's left edge is off-viewport (${rect.left})`).toBeGreaterThanOrEqual(0);
          expect(
            rect.right,
            `${label}: menu's right edge is past the viewport (${rect.right} > ${window.innerWidth})`,
          ).toBeLessThanOrEqual(window.innerWidth);

          const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
          expect(hit, `${label}: nothing hit-tests at the menu's own center`).not.toBeNull();
          expect(menu!.contains(hit), `${label}: the menu's center hit-tests to something outside it`).toBe(true);

          // Scoped to the header itself, not the whole document: this shell's
          // three-column desktop body grid (`.cols`, fixed 340px/330px rails)
          // is explicitly out of this sweep's scope (issue #171's own Out of
          // Scope — "the sweep targets the shared shells and their projected
          // chrome") — a forced-desktop phone is expected to overflow *there*,
          // the same way forcing desktop mode on a phone always has; what must
          // never overflow is the header chrome the escape hatch lives in.
          const header = root.querySelector<HTMLElement>('[data-testid="board-header"]')!;
          expect(
            header.scrollWidth,
            `${label}: the header overflows horizontally (${header.scrollWidth} > ${window.innerWidth})`,
          ).toBeLessThanOrEqual(window.innerWidth);
        }
      } finally {
        root.remove();
        stub.restore();
        window.removeEventListener('error', onError);
        window.removeEventListener('unhandledrejection', onRejection);
      }

      expect(pageErrors, `page errors fired during the sweep: ${pageErrors.join('; ')}`).toEqual([]);
    });
  }
});
