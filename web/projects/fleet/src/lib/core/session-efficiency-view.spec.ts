import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { SessionEfficiencyInput } from './session-efficiency';
import { SessionEfficiency } from './session-efficiency-view';

const USAGE: SessionEfficiencyInput = {
  costUsd: 0.3,
  estimatedCostUsd: null,
  costPartial: false,
  inputTokens: 20_000,
  outputTokens: 32_700,
  cacheReadTokens: 8_340_000,
  cacheCreateTokens: 200_000,
};

describe('SessionEfficiency', () => {
  async function render(usage: SessionEfficiencyInput): Promise<HTMLElement> {
    await TestBed.configureTestingModule({
      imports: [SessionEfficiency],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(SessionEfficiency);
    fixture.componentRef.setInput('usage', usage);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  it('renders each part as its own unbreakable span', async () => {
    const el = await render(USAGE);
    expect([...el.querySelectorAll('.part')].map((p) => p.textContent)).toEqual(['$0.30', '· 220kI', '/ 32.7kO', '× 2.57%']);
  });

  it('carries the spelled-out breakdown as the host title', async () => {
    const el = await render(USAGE);
    expect(el.getAttribute('title')).toMatch(/^session efficiency cost: \$0\.30 · 220,000 input/);
  });

  it('renders only the cost for a step with no tokens', async () => {
    const el = await render({ ...USAGE, costUsd: 0, inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, cacheCreateTokens: 0 });
    expect([...el.querySelectorAll('.part')].map((p) => p.textContent)).toEqual(['$0.00']);
  });
});
