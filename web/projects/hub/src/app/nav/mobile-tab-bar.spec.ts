import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { MobileTabBar } from './mobile-tab-bar';

describe('MobileTabBar', () => {
  let stub: RequestClientStub;

  afterEach(() => stub.restore());

  async function render(questions: readonly unknown[]) {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/questions') return questions;
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [MobileTabBar],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(MobileTabBar);
    await settle(fixture);
    return fixture;
  }

  it('renders Board, Asks, and Fleet, with Asks and Fleet inert', async () => {
    const fixture = await render([]);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="tab-board"]')?.textContent).toContain('Board');
    const asks = el.querySelector('[data-testid="tab-asks"]');
    const fleetTab = el.querySelector('[data-testid="tab-fleet"]');
    expect(asks?.textContent).toContain('Asks');
    expect(fleetTab?.textContent).toContain('Fleet');
    expect(asks?.hasAttribute('disabled')).toBe(true);
    expect(fleetTab?.hasAttribute('disabled')).toBe(true);
    expect(asks?.classList.contains('inert')).toBe(true);
    expect(fleetTab?.classList.contains('inert')).toBe(true);
  });

  it('omits the Asks badge when there are no open questions', async () => {
    const fixture = await render([]);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="tab-asks-badge"]')).toBeNull();
  });

  it('shows the live open-asks count on the Asks tab', async () => {
    const fixture = await render([
      { question_id: 'qn_1', chunk_id: 'ch_1', runner_id: 'r1', question: 'a?', options: [] },
      { question_id: 'qn_2', chunk_id: 'ch_2', runner_id: 'r2', question: 'b?', options: [] },
    ]);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="tab-asks-badge"]')?.textContent).toBe('2');
  });
});
