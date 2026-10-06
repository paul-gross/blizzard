import { RUNNER_NAME_SEPARATOR, runnerDisplayName, runnerTitle } from './runner-display-name';

describe('runnerDisplayName', () => {
  it('joins the compact id to the name with the separator', () => {
    expect(runnerDisplayName('rn_01KXKVVF1J3D6H6VYZ3XYNABF3', 'r-claude')).toBe('R-ABF3.r-claude');
    expect(RUNNER_NAME_SEPARATOR).toBe('.');
  });

  it('renders the compact id alone when the view carries no name', () => {
    expect(runnerDisplayName('rn_01KXKVVF1J3D6H6VYZ3XYNABF3')).toBe('R-ABF3');
    expect(runnerDisplayName('rn_01KXKVVF1J3D6H6VYZ3XYNABF3', null)).toBe('R-ABF3');
    expect(runnerDisplayName('rn_01KXKVVF1J3D6H6VYZ3XYNABF3', '')).toBe('R-ABF3');
  });

  it('keeps two runners sharing a name apart by their compact ids', () => {
    expect(runnerDisplayName('rn_01KXKVVF1J3D6H6VYZ3XYNABF3', 'r-claude')).not.toBe(
      runnerDisplayName('rn_01KXKVVF1J3D6H6VYZ3XYN7Q2M', 'r-claude'),
    );
  });
});

describe('runnerTitle', () => {
  it('titles a runner with its display name, then the full id it compacts', () => {
    expect(runnerTitle('rn_01KXKVVF1J3D6H6VYZ3XYNABF3', 'r-claude')).toBe('R-ABF3.r-claude · rn_01KXKVVF1J3D6H6VYZ3XYNABF3');
    expect(runnerTitle('rn_01KXKVVF1J3D6H6VYZ3XYNABF3')).toBe('R-ABF3 · rn_01KXKVVF1J3D6H6VYZ3XYNABF3');
  });
});
