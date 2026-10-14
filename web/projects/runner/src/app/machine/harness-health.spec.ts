import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { provideActiveRunner } from '../core/active-runner';
import { LocalHarnessHealth } from './harness-health';

describe('LocalHarnessHealth', () => {
  let stub: RequestClientStub;

  afterEach(() => stub.restore());

  async function render(items: readonly unknown[]): Promise<HTMLElement> {
    stub = stubRequestClient(runnerClient, (method, path) =>
      method === 'GET' && path === '/runners/rn_1/api/dashboard' ? { harness_health: { items } } : {},
    );
    await TestBed.configureTestingModule({
      imports: [LocalHarnessHealth],
      providers: [
        provideZonelessChangeDetection(),
        provideActiveRunner('rn_1'),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(LocalHarnessHealth);
    await settle(fixture);
    return fixture.nativeElement as HTMLElement;
  }

  it('renders an available and an unavailable binding, naming the cause', async () => {
    const el = await render([
      { harness_id: 'claude-code', available: true, version: '2.1.0', degradations: [] },
      { harness_id: 'opencode', available: false, cause: 'missing_binary' },
    ]);

    const rows = el.querySelectorAll('[data-testid="harness-health-row"]');
    expect(rows).toHaveLength(2);
    expect(rows[0].querySelector('[data-testid="harness-health-available"]')).not.toBeNull();
    expect(rows[1].querySelector('[data-testid="harness-health-unavailable"]')).not.toBeNull();
    expect(rows[1].querySelector('[data-testid="harness-health-cause"]')?.textContent?.trim()).toBe('missing_binary');
  });

  it('renders the empty state with no bindings', async () => {
    const el = await render([]);

    expect(el.querySelector('[data-testid="harness-health-empty"]')).not.toBeNull();
  });
});
