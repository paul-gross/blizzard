import { parseProjectTab } from './project-overview.model';

describe('parseProjectTab', () => {
  it.each(['overview', 'sources', 'repositories'])('names the %s tab', (tab) => {
    expect(parseProjectTab(tab)).toBe(tab);
  });

  it('names no tab for an unknown string', () => {
    expect(parseProjectTab('history')).toBeNull();
    expect(parseProjectTab('')).toBeNull();
  });
});
