import type { Urgency } from 'fleet';
import type { Project } from '../../core/project.domain';
import { projectOptions, urgencyOptions } from './new-hub-item.model';

describe('new hub item options', () => {
  it('maps projects by their stable id and name', () => {
    const base: Project = { project_id: 'p1', slug: 'first', name: 'First', description: '', color: '#123456', revision: 1, created_at: '2026-01-01T00:00:00Z' };
    const projects: Project[] = [base, { ...base, project_id: 'p2', name: 'Second' }];
    expect(projectOptions(projects)).toEqual([{ value: 'p1', label: 'First' }, { value: 'p2', label: 'Second' }]);
    expect(projectOptions([])).toEqual([]);
  });
  it('keeps live urgencies in order and excludes retired ones', () => {
    const urgencies = [
      { id: 'u1', name: 'Urgent', retired: false },
      { id: 'old', name: 'Old', retired: true },
      { id: 'u2', name: 'Standard', retired: false },
    ] as Urgency[];
    expect(urgencyOptions(urgencies)).toEqual([{ value: 'u1', label: 'Urgent' }, { value: 'u2', label: 'Standard' }]);
    expect(urgencyOptions([])).toEqual([]);
  });
});
