import { formatSessionEfficiency, sessionEfficiencyParts, sessionEfficiencyTitle, type SessionEfficiencyInput } from './session-efficiency';

const usage = (overrides: Partial<SessionEfficiencyInput> = {}): SessionEfficiencyInput => ({
  costUsd: 0.3,
  estimatedCostUsd: null,
  costPartial: false,
  inputTokens: 20_000,
  outputTokens: 32_700,
  cacheReadTokens: 0,
  cacheCreateTokens: 0,
  ...overrides,
});

describe('sessionEfficiencyParts', () => {
  it('reads the cost, distinct input, output and cache miss, each separator leading its part', () => {
    const parts = sessionEfficiencyParts(
      usage({ inputTokens: 20_000, cacheCreateTokens: 200_000, outputTokens: 32_700, cacheReadTokens: 8_340_000 }),
    );
    expect(parts).toEqual(['$0.30', '· 220kI', '/ 32.7kO', '× 2.57%']);
  });

  it('counts cache write as distinct input and cache read as neither input nor output', () => {
    const parts = sessionEfficiencyParts(usage({ inputTokens: 100, cacheCreateTokens: 400, outputTokens: 0, cacheReadTokens: 500 }));
    expect(parts).toEqual(['$0.30', '· 500I', '/ 0O', '× 50.0%']);
  });

  it('reads a cost-only figure for a step with no tokens at all — `$0.00`', () => {
    expect(sessionEfficiencyParts(usage({ costUsd: 0, inputTokens: 0, outputTokens: 0 }))).toEqual(['$0.00']);
    expect(formatSessionEfficiency(usage({ costUsd: 0, inputTokens: 0, outputTokens: 0 }))).toBe('$0.00');
  });

  it('carries the estimate and partial marks formatCost owns', () => {
    expect(sessionEfficiencyParts(usage({ costUsd: 0.1, estimatedCostUsd: 0.2, costPartial: true }))[0]).toBe('~$0.30+');
  });

  it('reads a miss of `—` when output is the only token class', () => {
    expect(sessionEfficiencyParts(usage({ inputTokens: 0, outputTokens: 50 }))).toEqual(['$0.30', '· 0I', '/ 50O', '× —']);
  });
});

describe('token counts to three significant figures', () => {
  const input = (inputTokens: number): string => sessionEfficiencyParts(usage({ inputTokens }))[1];

  it('reads a count under a thousand exactly', () => {
    expect(input(850)).toBe('· 850I');
    expect(input(999)).toBe('· 999I');
  });

  it('reads thousands with a `k` suffix', () => {
    expect(input(1_000)).toBe('· 1.00kI');
    expect(input(32_749)).toBe('· 32.7kI');
    expect(input(220_000)).toBe('· 220kI');
  });

  it('reads millions with an `M` suffix, rolling a count that rounds to 1000k over', () => {
    expect(input(1_234_567)).toBe('· 1.23MI');
    expect(input(999_999)).toBe('· 1.00MI');
    expect(input(45_600_000)).toBe('· 45.6MI');
  });
});

describe('cache miss percent', () => {
  const miss = (inputTokens: number, cacheReadTokens: number): string =>
    sessionEfficiencyParts(usage({ inputTokens, cacheReadTokens }))[3];

  it('reads to three significant figures', () => {
    expect(miss(412, 99_588)).toBe('× 0.412%');
    expect(miss(123, 877)).toBe('× 12.3%');
  });

  it('reads `0` with every input token a cache hit, and `100` with none', () => {
    expect(miss(0, 1_000)).toBe('× 0%');
    expect(miss(1_000, 0)).toBe('× 100%');
  });

  it('reads `100` for a miss that rounds up to a hundred', () => {
    expect(miss(99_999, 1)).toBe('× 100%');
  });
});

describe('formatSessionEfficiency', () => {
  it('joins the parts with single spaces', () => {
    expect(formatSessionEfficiency(usage({ inputTokens: 1_000, cacheReadTokens: 1_000, outputTokens: 500 }))).toBe(
      '$0.30 · 1.00kI / 500O × 50.0%',
    );
  });
});

describe('sessionEfficiencyTitle', () => {
  it('spells every count out in full', () => {
    expect(sessionEfficiencyTitle(usage({ inputTokens: 20_000, cacheCreateTokens: 200_000, outputTokens: 32_700, cacheReadTokens: 8_340_000 }))).toBe(
      'session efficiency cost: $0.30 · 220,000 input (in + cache write) · 32,700 output · 8,340,000 cache read · 2.57% cache miss',
    );
  });

  it('reads `—` for the miss with no input at all', () => {
    expect(sessionEfficiencyTitle(usage({ inputTokens: 0, outputTokens: 0 }))).toContain('· — cache miss');
  });
});
