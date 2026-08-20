import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { ChunkDetail } from '../api/hub';
import { ChunkTokenBreakdown } from './chunk-token-breakdown';

const COST_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01cost00000000000000000000000',
  graph_id: 'gr_1',
  status: 'running',
  current_node_id: 'nd_review',
  latest_epoch: 2,
  work_refs: [],
  history: [],
  artifacts: [],
  cost: {
    input_tokens: 1200,
    output_tokens: 800,
    cache_read_tokens: 300,
    cache_create_tokens: 100,
    cost_usd: 0.42,
    cost_partial: false,
  },
};

const PARTIAL_COST_DETAIL: ChunkDetail = {
  ...COST_DETAIL,
  chunk_id: 'ch_01partial00000000000000000000',
  cost: {
    input_tokens: 100,
    output_tokens: 50,
    cache_read_tokens: 0,
    cache_create_tokens: 0,
    cost_usd: 0.1,
    cost_partial: true,
  },
};

describe('ChunkTokenBreakdown', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ChunkTokenBreakdown],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders the chunk-total cost and all four token counts inline, with no expand toggle', async () => {
    const fixture = TestBed.createComponent(ChunkTokenBreakdown);
    fixture.componentRef.setInput('detail', COST_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="cost-total-usd"]')?.textContent).toContain('$0.42');
    expect(el.querySelector('[data-testid="cost-partial-badge"]')).toBeNull();

    // Every token count is visible without interaction, one labelled row apiece — no
    // expand toggle left to click.
    expect(el.querySelector('[data-testid="fact-tokens-input"]')?.textContent).toContain('1.2k');
    expect(el.querySelector('[data-testid="fact-tokens-output"]')?.textContent).toContain('800');
    expect(el.querySelector('[data-testid="fact-tokens-cache-read"]')?.textContent).toContain('300');
    expect(el.querySelector('[data-testid="fact-tokens-cache-creation"]')?.textContent).toContain('100');
    expect(el.querySelector('[data-testid="tokens-expand-toggle"]')).toBeNull();
    expect(el.querySelector('[data-testid="tokens-breakdown"]')).toBeNull();
  });

  it('marks the chunk-total cost as PARTIAL when the derived total is a lower bound', async () => {
    const fixture = TestBed.createComponent(ChunkTokenBreakdown);
    fixture.componentRef.setInput('detail', PARTIAL_COST_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="cost-total-usd"]')?.textContent).toContain('~$0.10');
    expect(el.querySelector('[data-testid="cost-partial-badge"]')).not.toBeNull();
  });

  it('defaults to a zero, non-partial total when the detail carries no cost yet', async () => {
    const fixture = TestBed.createComponent(ChunkTokenBreakdown);
    fixture.componentRef.setInput('detail', { ...COST_DETAIL, cost: undefined });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="cost-total-usd"]')?.textContent).toContain('$0.00');
    expect(el.querySelector('[data-testid="cost-partial-badge"]')).toBeNull();
    expect(el.querySelector('[data-testid="fact-tokens-input"]')?.textContent).toContain('0');
  });

  it('mutes a zero token count and leaves a non-zero one at full strength, regardless of which class it is', async () => {
    const fixture = TestBed.createComponent(ChunkTokenBreakdown);
    fixture.componentRef.setInput('detail', PARTIAL_COST_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    // PARTIAL_COST_DETAIL: input/output non-zero, cache read/creation both zero.
    expect(el.querySelector('[data-testid="fact-tokens-input"]')?.classList.contains('zero')).toBe(false);
    expect(el.querySelector('[data-testid="fact-tokens-output"]')?.classList.contains('zero')).toBe(false);
    expect(el.querySelector('[data-testid="fact-tokens-cache-read"]')?.classList.contains('zero')).toBe(true);
    expect(el.querySelector('[data-testid="fact-tokens-cache-creation"]')?.classList.contains('zero')).toBe(true);
  });

  it('does not mute a non-zero cache count — the rule is "zero", not "cache"', async () => {
    const fixture = TestBed.createComponent(ChunkTokenBreakdown);
    fixture.componentRef.setInput('detail', {
      ...COST_DETAIL,
      cost: { ...COST_DETAIL.cost!, cache_read_tokens: 300, cache_create_tokens: 100 },
    });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="fact-tokens-cache-read"]')?.classList.contains('zero')).toBe(false);
    expect(el.querySelector('[data-testid="fact-tokens-cache-creation"]')?.classList.contains('zero')).toBe(false);
  });
});
