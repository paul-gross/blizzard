import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hostRunnerView, hubConnectivityView, runnerDashboardView, runnerStatusView, settle, stubRunnerClient } from 'fleet/testing';
import { page } from 'vitest/browser';

import { HubPanel } from './hub-panel';

describe('hub panel row spacing', () => {
  it('keeps a 6px gap between the link, issues and fleet at phone and desktop widths', async () => {
    const stub = stubRunnerClient({
      'GET /api/host/runners': () => ({ items: [hostRunnerView({ runner_id: 'r1' }), hostRunnerView({ runner_id: 'r2' })] }),
      'GET /runners/{runner_id}/api/dashboard': (_request, { runner_id }) => runnerDashboardView({
        runner: runnerStatusView({ runner_id, hub: hubConnectivityView({ reachable: false }) }),
        fleet_summary: { ready: 1, running: 2, waiting: 3, needs: 4 },
      }),
    });
    await TestBed.configureTestingModule({
      imports: [HubPanel],
      providers: [provideZonelessChangeDetection(), provideRouter([]), provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } }))],
    }).compileComponents();
    const fixture = TestBed.createComponent(HubPanel);
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    try {
      await settle(fixture);
      for (const width of [390, 1280]) {
        await page.viewport(width, 800);
        const rows = Array.from(root.querySelectorAll<HTMLElement>('.link-line, [data-testid="hub-issue"], [data-testid="fleet-strip"]'));
        expect(rows).toHaveLength(4);
        for (let i = 1; i < rows.length; i++) {
          expect(rows[i].getBoundingClientRect().top - rows[i - 1].getBoundingClientRect().bottom).toBeCloseTo(6, 1);
        }
      }
    } finally {
      root.remove();
      fixture.destroy();
      stub.restore();
    }
  });
});
