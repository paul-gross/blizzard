import { TestBed } from '@angular/core/testing';

import { RUNNER_HOST_NAMES, RUNNER_NAME_SEPARATOR, injectRunnerHostNames, runnerBoardParts, runnerDisplayName, runnerTitle } from './runner-display-name';

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

  it('names the runner host/name where its host is known, the compact id standing in for a missing name', () => {
    expect(runnerDisplayName('rn_01KXKVVF1J3D6H6VYZ3XYNABF3', 'r-claude', 'atlas')).toBe('atlas/r-claude');
    expect(runnerDisplayName('rn_01KXKVVF1J3D6H6VYZ3XYNABF3', null, 'atlas')).toBe('atlas/R-ABF3');
    expect(runnerDisplayName('rn_01KXKVVF1J3D6H6VYZ3XYNABF3', 'r-claude', null)).toBe('R-ABF3.r-claude');
    expect(runnerDisplayName('rn_01KXKVVF1J3D6H6VYZ3XYNABF3', 'r-claude', '')).toBe('R-ABF3.r-claude');
  });
});

describe('runnerBoardParts', () => {
  it('names a named runner by its host and name apart, leaving the compact id out', () => {
    expect(runnerBoardParts('rn_01KXKVVF1J3D6H6VYZ3XYNABF3', 'r-claude', 'atlas')).toEqual({ host: 'atlas', name: 'r-claude' });
  });

  it('holds no host where it is unknown', () => {
    expect(runnerBoardParts('rn_01KXKVVF1J3D6H6VYZ3XYNABF3', 'r-claude')).toEqual({ host: null, name: 'r-claude' });
    expect(runnerBoardParts('rn_01KXKVVF1J3D6H6VYZ3XYNABF3', 'r-claude', '')).toEqual({ host: null, name: 'r-claude' });
  });

  it('falls back to the display name, compact id and all, for a runner with no name', () => {
    expect(runnerBoardParts('rn_01KXKVVF1J3D6H6VYZ3XYNABF3', null)).toEqual({ host: null, name: 'R-ABF3' });
    expect(runnerBoardParts('rn_01KXKVVF1J3D6H6VYZ3XYNABF3', null, 'atlas')).toEqual({ host: null, name: 'atlas/R-ABF3' });
  });
});

describe('runnerTitle', () => {
  it('titles a runner with its display name, then the full id it compacts', () => {
    expect(runnerTitle('rn_01KXKVVF1J3D6H6VYZ3XYNABF3', 'r-claude')).toBe('R-ABF3.r-claude · rn_01KXKVVF1J3D6H6VYZ3XYNABF3');
    expect(runnerTitle('rn_01KXKVVF1J3D6H6VYZ3XYNABF3')).toBe('R-ABF3 · rn_01KXKVVF1J3D6H6VYZ3XYNABF3');
    expect(runnerTitle('rn_01KXKVVF1J3D6H6VYZ3XYNABF3', 'r-claude', 'atlas')).toBe('atlas/r-claude · rn_01KXKVVF1J3D6H6VYZ3XYNABF3');
  });
});

describe('RUNNER_HOST_NAMES', () => {
  it('knows no host by default, so an app that provides none keeps the compact-id form', () => {
    const hosts = TestBed.runInInjectionContext(() => injectRunnerHostNames());
    expect(hosts().size).toBe(0);
  });

  it("reads the app's provided map", () => {
    TestBed.configureTestingModule({
      providers: [{ provide: RUNNER_HOST_NAMES, useValue: () => new Map([['rn_1', 'atlas']]) }],
    });
    const hosts = TestBed.runInInjectionContext(() => injectRunnerHostNames());
    expect(hosts().get('rn_1')).toBe('atlas');
  });
});
