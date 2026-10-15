import { projectToOpen } from './project-open.model';

const base = { selected: null, lensId: null, rows: ['proj_b', 'proj_w'], settled: true, mobile: false };

describe('projectToOpen', () => {
  it('opens the first project on All at desktop width', () => {
    expect(projectToOpen(base)).toBe('proj_b');
  });

  it('stays on the list on a phone', () => {
    expect(projectToOpen({ ...base, mobile: true })).toBeNull();
  });

  it('waits for the projects read', () => {
    expect(projectToOpen({ ...base, rows: [], settled: false })).toBeNull();
  });

  it('opens nothing when the tenant holds no project', () => {
    expect(projectToOpen({ ...base, rows: [] })).toBeNull();
  });

  it('leaves a project the URL names alone', () => {
    expect(projectToOpen({ ...base, selected: 'proj_w' })).toBeNull();
  });

  it('opens the lensed project under a lens, on a phone too', () => {
    expect(projectToOpen({ ...base, lensId: 'proj_w', mobile: true })).toBe('proj_w');
  });
});
