import { chunkDetail } from 'fleet/testing';

import { openProjectColor, projectColorOf } from './project-lens-colors';

describe('projectColorOf', () => {
  const projects = [
    { project_id: 'first', color: '#123456' },
    { project_id: 'second', color: '#abcdef' },
  ];

  it('looks up the requested project when colors are shown', () => {
    expect(projectColorOf(true, projects, 'second')).toBe('#abcdef');
  });

  it('hides colors when the gate is closed', () => {
    expect(projectColorOf(false, projects, 'second')).toBeNull();
  });

  it('returns null for an unavailable list or missing project', () => {
    expect(projectColorOf(true, undefined, 'second')).toBeNull();
    expect(projectColorOf(true, projects, 'unknown')).toBeNull();
    expect(projectColorOf(true, projects, undefined)).toBeNull();
    expect(projectColorOf(true, projects, null)).toBeNull();
    expect(projectColorOf(true, projects, '')).toBeNull();
  });

  it('uses the same lookup for the open chunk', () => {
    expect(openProjectColor(true, chunkDetail({ project_id: 'second' }), projects)).toBe('#abcdef');
    expect(openProjectColor(true, undefined, projects)).toBeNull();
  });
});
