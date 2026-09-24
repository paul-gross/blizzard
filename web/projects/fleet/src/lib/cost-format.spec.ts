import { formatCost, formatTokens, hasCostFigure } from './cost-format';

describe('formatCost', () => {
  it('renders neither marker when the total is fully billed and not partial', () => {
    expect(formatCost(4, null, false)).toBe('$4.00');
    expect(formatCost(4, undefined, false)).toBe('$4.00');
  });

  it('prefixes ~ when an estimate is present, folding it into the one figure', () => {
    expect(formatCost(4, 0.05, false)).toBe('~$4.05');
  });

  it('suffixes + when the total is partial, with no estimate present', () => {
    expect(formatCost(4, null, true)).toBe('$4.00+');
  });

  it('combines both markers when the total is both estimated and partial', () => {
    expect(formatCost(4, 0.05, true)).toBe('~$4.05+');
  });

  it('renders an entirely estimated total (nothing billed yet) as the estimate alone', () => {
    expect(formatCost(0, 0.07, false)).toBe('~$0.07');
  });

  it('still marks an estimate of exactly 0 with ~, since the amount is estimated rather than absent', () => {
    expect(formatCost(4, 0, false)).toBe('~$4.00');
  });
});

describe('hasCostFigure', () => {
  it('is false only for an entirely empty total: nothing billed, no estimate, not partial', () => {
    expect(hasCostFigure(0, null, false)).toBe(false);
    expect(hasCostFigure(0, undefined, false)).toBe(false);
  });

  it('is true on a billed amount alone', () => {
    expect(hasCostFigure(0.42, null, false)).toBe(true);
  });

  it('is true on an estimate alone, even one of exactly 0', () => {
    expect(hasCostFigure(0, 0.07, false)).toBe(true);
    expect(hasCostFigure(0, 0, false)).toBe(true);
  });

  it('is true on a partial mark alone, so a $0.00+ lower bound still shows', () => {
    expect(hasCostFigure(0, null, true)).toBe(true);
  });
});

describe('formatTokens', () => {
  it('renders a count under 1000 exactly', () => {
    expect(formatTokens(0)).toBe('0');
    expect(formatTokens(999)).toBe('999');
  });

  it('renders a count in the thousands to one decimal place, suffixed k', () => {
    expect(formatTokens(1_000)).toBe('1.0k');
    expect(formatTokens(3_400)).toBe('3.4k');
  });

  it('renders a count in the millions to one decimal place, suffixed M', () => {
    expect(formatTokens(1_000_000)).toBe('1.0M');
    expect(formatTokens(3_400_000)).toBe('3.4M');
  });
});
