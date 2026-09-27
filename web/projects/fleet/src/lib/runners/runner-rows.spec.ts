import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { client as hubClient } from '../api/hub/client.gen';
import { settle } from '../testing/settle';
import { type RequestClientStub, stubRequestClient } from '../testing/stub-request-client';
import {
  FRESHNESS_AGING_AFTER_MS,
  FRESHNESS_STALE_AFTER_MS,
  classifySubscriptionFreshness,
  injectRunnerRows,
  windowElapsedPct,
} from './runner-rows';

describe('windowElapsedPct', () => {
  const FIVE_H_SECONDS = 5 * 60 * 60;

  it('reads ~0 right at the window\'s own start (just reset)', () => {
    const resetsAt = '2026-07-16T17:00:00.000Z';
    const nowMs = Date.parse('2026-07-16T12:00:00.000Z'); // exactly resetsAt - 5h
    expect(windowElapsedPct(nowMs, resetsAt, FIVE_H_SECONDS)).toBe(0);
  });

  it('reads ~100 right at the instant the window resets (about to reset)', () => {
    const resetsAt = '2026-07-16T17:00:00.000Z';
    const nowMs = Date.parse(resetsAt);
    expect(windowElapsedPct(nowMs, resetsAt, FIVE_H_SECONDS)).toBe(100);
  });

  it('reads the midpoint at half the window elapsed', () => {
    const resetsAt = '2026-07-16T17:00:00.000Z';
    const nowMs = Date.parse('2026-07-16T14:30:00.000Z'); // resetsAt - 2.5h
    expect(windowElapsedPct(nowMs, resetsAt, FIVE_H_SECONDS)).toBe(50);
  });

  it('clamps to 0 for a window that has not started yet', () => {
    const resetsAt = '2026-07-16T17:00:00.000Z';
    const nowMs = Date.parse('2026-07-16T11:00:00.000Z'); // an hour before the window starts
    expect(windowElapsedPct(nowMs, resetsAt, FIVE_H_SECONDS)).toBe(0);
  });

  it('clamps to 100 for a resets_at already in the past (a stale sample)', () => {
    const resetsAt = '2026-07-16T17:00:00.000Z';
    const nowMs = Date.parse('2026-07-16T18:00:00.000Z'); // an hour past reset
    expect(windowElapsedPct(nowMs, resetsAt, FIVE_H_SECONDS)).toBe(100);
  });

  it('reads 0 for an unparseable resets_at rather than throwing', () => {
    expect(windowElapsedPct(Date.now(), 'not-a-date', FIVE_H_SECONDS)).toBe(0);
  });
});

describe('classifySubscriptionFreshness', () => {
  it('reads fresh at exactly 15m', () => {
    expect(classifySubscriptionFreshness(FRESHNESS_AGING_AFTER_MS)).toBe('fresh');
  });

  it('reads aging just past 15m', () => {
    expect(classifySubscriptionFreshness(FRESHNESS_AGING_AFTER_MS + 1)).toBe('aging');
  });

  it('reads aging at exactly 60m', () => {
    expect(classifySubscriptionFreshness(FRESHNESS_STALE_AFTER_MS)).toBe('aging');
  });

  it('reads stale just past 60m', () => {
    expect(classifySubscriptionFreshness(FRESHNESS_STALE_AFTER_MS + 1)).toBe('stale');
  });
});

@Component({ selector: 'fleet-test-runner-rows-host', template: `` })
class TestHost {
  readonly rows = injectRunnerRows().rows;
}

describe('injectRunnerRows subscription freshness fold', () => {
  const SAMPLED_AT = '2026-07-16T11:44:00.000Z';

  let stub: RequestClientStub;

  function runnersResponse(subscriptions: readonly Record<string, unknown>[]) {
    return {
      runners: [
        {
          runner_id: 'rn_tick',
          workspace_id: 'ws_a',
          registered_at: SAMPLED_AT,
          last_seen_at: SAMPLED_AT,
          online: true,
          hub_paused: false,
          locally_paused: false,
          subscriptions,
        },
      ],
    };
  }

  afterEach(() => {
    stub.restore();
    vi.useRealTimers();
  });

  it("reclassifies a member's tier when the now signal advances, with no new query data", async () => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/runners') {
        return runnersResponse([
          { slug: 'anthropic', name: 'Anthropic', sampled_at: SAMPLED_AT, windows: [], condition: null },
        ]);
      }
      if (method === 'GET' && path === '/api/chunks') return { chunks: [], next_cursor: null };
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();

    // Only Date/setInterval are faked — `settle`'s own real `setTimeout` still runs, so
    // the query's stubbed fetch keeps resolving normally.
    vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] });
    vi.setSystemTime(Date.parse(SAMPLED_AT));

    const fixture = TestBed.createComponent(TestHost);
    await settle(fixture);
    expect(fixture.componentInstance.rows()[0]?.subscriptionPaces[0]?.freshness).toBe('fresh');

    vi.setSystemTime(Date.parse(SAMPLED_AT) + FRESHNESS_AGING_AFTER_MS + 60_000);
    vi.advanceTimersByTime(30_000); // injectRunnerRows' own now-signal tick period
    fixture.detectChanges();

    expect(fixture.componentInstance.rows()[0]?.subscriptionPaces[0]?.freshness).toBe('aging');
  });

  it('gives no tier and no refreshed label for an unparseable sampled_at', async () => {
    stub = stubRequestClient(hubClient, (method, path) => {
      if (method === 'GET' && path === '/api/runners') {
        return runnersResponse([
          { slug: 'anthropic', name: 'Anthropic', sampled_at: 'not-a-date', windows: [], condition: null },
        ]);
      }
      if (method === 'GET' && path === '/api/chunks') return { chunks: [], next_cursor: null };
      return {};
    });
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      ],
    }).compileComponents();

    const fixture = TestBed.createComponent(TestHost);
    await settle(fixture);

    const member = fixture.componentInstance.rows()[0]?.subscriptionPaces[0];
    expect(member?.freshness).toBeNull();
    expect(member?.refreshedLabel).toBeNull();
  });
});
