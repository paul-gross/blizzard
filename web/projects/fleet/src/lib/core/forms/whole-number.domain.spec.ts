import { readWholeNumber } from './whole-number.domain';

describe('readWholeNumber', () => {
  it('reads zero and plain digits', () => {
    expect(readWholeNumber('0')).toBe(0);
    expect(readWholeNumber('48')).toBe(48);
  });
  it('reads leading zeros as the number', () => {
    expect(readWholeNumber('007')).toBe(7);
    expect(readWholeNumber('000')).toBe(0);
  });
  it('reads the largest safe integer and rejects the next', () => {
    expect(readWholeNumber('9007199254740991')).toBe(9007199254740991);
    expect(readWholeNumber('9007199254740992')).toBeNull();
  });
  it('rejects overflow digits', () => {
    expect(readWholeNumber('9'.repeat(400))).toBeNull();
  });
  it.each([
    ['blank', ''],
    ['signs', '-1'],
    ['plus sign', '+1'],
    ['fraction', '1.5'],
    ['trailing dot', '1.'],
    ['exponent', '1e3'],
    ['leading space', ' 1'],
    ['trailing space', '1 '],
    ['trailing newline', '1\n'],
    ['inner space', '1 2'],
    ['hex', '0x10'],
    ['word', 'abc'],
    ['non-ascii digits', '١٢'],
    ['infinity', 'Infinity'],
  ])('rejects %s as null, never a number', (_name, text) => {
    expect(readWholeNumber(text)).toBeNull();
  });
});
