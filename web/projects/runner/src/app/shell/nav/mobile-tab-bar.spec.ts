import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { MobileTabBar } from './mobile-tab-bar';

describe('MobileTabBar', () => {
  let stub: RequestClientStub;

  afterEach(() => stub.restore());

  async function render(asks: readonly unknown[]) {
    stub = stubRequestClient(runnerClient, (method, path) => {
      if (method === 'GET' && path === '/api/dashboard') return { asks: { items: asks } };
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

  it('renders Board, Asks, Transcripts, and Events, with Asks and Transcripts inert', async () => {
    const fixture = await render([]);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="tab-board"]')?.textContent).toContain('Board');
    expect(el.querySelector('[data-testid="tab-events"]')?.textContent).toContain('Events');
    const asks = el.querySelector('[data-testid="tab-asks-runner"]');
    const transcripts = el.querySelector('[data-testid="tab-transcripts-runner"]');
    expect(asks?.textContent).toContain('Asks');
    expect(transcripts?.textContent).toContain('Transcripts');
    expect(asks?.hasAttribute('disabled')).toBe(true);
    expect(transcripts?.hasAttribute('disabled')).toBe(true);
    expect(asks?.classList.contains('inert')).toBe(true);
    expect(transcripts?.classList.contains('inert')).toBe(true);
  });

  it('omits the Asks badge when there are no open asks', async () => {
    const fixture = await render([]);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="tab-asks-runner-badge"]')).toBeNull();
  });

  it('shows the live open-asks count on the Asks tab', async () => {
    const fixture = await render([
      { question_id: 'qn_1', chunk_id: 'ch_1', lease_id: 'lease_1', question: 'a?', options: [], session_id: null, asked_at: null },
      { question_id: 'qn_2', chunk_id: 'ch_2', lease_id: 'lease_2', question: 'b?', options: [], session_id: null, asked_at: null },
    ]);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="tab-asks-runner-badge"]')?.textContent).toBe('2');
  });
});
