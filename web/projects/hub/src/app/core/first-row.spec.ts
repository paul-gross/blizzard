import { firstRowToOpen } from './first-row';

describe('firstRowToOpen', () => {
  it('opens the first row of a settled list on desktop when the URL names none', () => {
    expect(firstRowToOpen(null, ['hub-api', 'cli'], true, false)).toBe('hub-api');
  });

  it('leaves a named row alone', () => {
    expect(firstRowToOpen('cli', ['hub-api', 'cli'], true, false)).toBeNull();
  });

  it('waits for the read to settle', () => {
    expect(firstRowToOpen(null, [], false, false)).toBeNull();
  });

  it('opens nothing on an empty list', () => {
    expect(firstRowToOpen(null, [], true, false)).toBeNull();
  });

  it('keeps the list screen on a phone', () => {
    expect(firstRowToOpen(null, ['hub-api'], true, true)).toBeNull();
  });
});
