import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { FleetSpend } from './fleet-spend';
import type { FleetSpend as FleetSpendData } from './fleet-spend.domain';
import { fleetSpend } from './fleet-spend.builders';

const parts = (el: Element | null): (string | undefined)[] => [...(el?.querySelectorAll('.part') ?? [])].map((p) => p.textContent?.trim());
const spend = () => fleetSpend({ cost_usd: 27.5, input_tokens: 310_000, output_tokens: 120_000, cache_read_tokens: 9_400_000, cache_create_tokens: 640_000 });

describe('FleetSpend', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [FleetSpend], providers: [provideZonelessChangeDetection()] }).compileComponents();
  });

  async function render(today: FleetSpendData, yesterday: FleetSpendData | null = null, layout?: 'columns' | 'stacked') {
    const fixture = TestBed.createComponent(FleetSpend);
    fixture.componentRef.setInput('today', today);
    fixture.componentRef.setInput('yesterday', yesterday);
    if (layout) fixture.componentRef.setInput('layout', layout);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  it('shows Today as the session efficiency figure, its breakdown on hover', async () => {
    const el = await render(spend());

    expect([...el.querySelectorAll('.label')].map((l) => l.textContent)).toEqual(['Today']);
    const today = el.querySelector('[data-testid="glance-spend-value"]');
    // 310k input + 640k cache write = 950k distinct; 950k / (950k + 9.4M) cache read = 9.18% miss.
    expect(parts(today)).toEqual(['$27.50', '· 950kI', '/ 120kO', '× 9.18%']);
    expect(today?.getAttribute('title')).toContain('950,000 input (in + cache write)');
  });

  it('adds Yesterday under Today once its read lands', async () => {
    const el = await render(spend(), fleetSpend({ cost_usd: 3, estimated_cost_usd: 0.25 }));

    expect([...el.querySelectorAll('.label')].map((l) => l.textContent)).toEqual(['Today', 'Yesterday']);
    expect(parts(el.querySelector('[data-testid="glance-spend-yesterday"]'))[0]).toBe('~$3.25');
  });

  it('marks a partial window as a lower bound', async () => {
    const el = await render({ ...spend(), cost_partial: true });

    expect(parts(el.querySelector('[data-testid="glance-spend-value"]'))[0]).toBe('$27.50+');
  });

  it('lays out in columns by default, and stacks on request', async () => {
    const fixture = TestBed.createComponent(FleetSpend);
    fixture.componentRef.setInput('today', spend());
    await fixture.whenStable();
    const host = fixture.nativeElement as HTMLElement;
    expect(host.classList).not.toContain('stacked');

    fixture.componentRef.setInput('layout', 'stacked');
    await fixture.whenStable();
    expect(host.classList).toContain('stacked');
  });
});
