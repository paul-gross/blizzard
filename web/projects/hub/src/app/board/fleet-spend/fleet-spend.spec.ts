import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { FleetSpendView } from 'fleet';
import { fleetSpendView } from 'fleet/testing';
import { FleetSpend, spendEfficiency } from './fleet-spend';

const parts = (el: Element | null): (string | undefined)[] => [...(el?.querySelectorAll('.part') ?? [])].map((p) => p.textContent?.trim());

describe('spendEfficiency', () => {
  it('reads a spend as the session efficiency input, field for field', () => {
    const spend = fleetSpendView({ cost_usd: 1.25, estimated_cost_usd: 0.5, cost_partial: true });

    expect(spendEfficiency(spend)).toEqual({
      costUsd: 1.25,
      estimatedCostUsd: 0.5,
      costPartial: true,
      inputTokens: spend.input_tokens,
      outputTokens: spend.output_tokens,
      cacheReadTokens: spend.cache_read_tokens,
      cacheCreateTokens: spend.cache_create_tokens,
    });
  });
});

describe('FleetSpend', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [FleetSpend], providers: [provideZonelessChangeDetection()] }).compileComponents();
  });

  async function render(today: FleetSpendView, yesterday: FleetSpendView | null = null, layout?: 'columns' | 'stacked') {
    const fixture = TestBed.createComponent(FleetSpend);
    fixture.componentRef.setInput('today', today);
    fixture.componentRef.setInput('yesterday', yesterday);
    if (layout) fixture.componentRef.setInput('layout', layout);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  it('shows Today as the session efficiency figure, its breakdown on hover', async () => {
    const el = await render(fleetSpendView());

    expect([...el.querySelectorAll('.label')].map((l) => l.textContent)).toEqual(['Today']);
    const today = el.querySelector('[data-testid="glance-spend-value"]');
    // 310k input + 640k cache write = 950k distinct; 950k / (950k + 9.4M) cache read = 9.18% miss.
    expect(parts(today)).toEqual(['$27.50', '· 950kI', '/ 120kO', '× 9.18%']);
    expect(today?.getAttribute('title')).toContain('950,000 input (in + cache write)');
  });

  it('adds Yesterday under Today once its read lands', async () => {
    const el = await render(fleetSpendView(), fleetSpendView({ cost_usd: 3, estimated_cost_usd: 0.25 }));

    expect([...el.querySelectorAll('.label')].map((l) => l.textContent)).toEqual(['Today', 'Yesterday']);
    expect(parts(el.querySelector('[data-testid="glance-spend-yesterday"]'))[0]).toBe('~$3.25');
  });

  it('marks a partial window as a lower bound', async () => {
    const el = await render(fleetSpendView({ cost_partial: true }));

    expect(parts(el.querySelector('[data-testid="glance-spend-value"]'))[0]).toBe('$27.50+');
  });

  it('lays out in columns by default, and stacks on request', async () => {
    const fixture = TestBed.createComponent(FleetSpend);
    fixture.componentRef.setInput('today', fleetSpendView());
    await fixture.whenStable();
    const host = fixture.nativeElement as HTMLElement;
    expect(host.classList).not.toContain('stacked');

    fixture.componentRef.setInput('layout', 'stacked');
    await fixture.whenStable();
    expect(host.classList).toContain('stacked');
  });
});
