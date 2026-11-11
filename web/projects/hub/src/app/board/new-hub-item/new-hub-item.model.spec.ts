import type { ProjectView, UrgencyView } from 'fleet';
import { projectOptions, urgencyOptions } from './new-hub-item.model';

describe('new hub item options', () => {
  it('maps projects by their stable id and name', () => {
    const projects = [{ project_id: 'p1', name: 'First' }, { project_id: 'p2', name: 'Second' }] as ProjectView[];
    expect(projectOptions(projects)).toEqual([{ value: 'p1', label: 'First' }, { value: 'p2', label: 'Second' }]);
    expect(projectOptions([])).toEqual([]);
  });
  it('keeps live urgencies in order and excludes retired ones', () => {
    const urgencies = [
      { urgency_id: 'u1', name: 'Urgent', retired: false },
      { urgency_id: 'old', name: 'Old', retired: true },
      { urgency_id: 'u2', name: 'Standard', retired: false },
    ] as UrgencyView[];
    expect(urgencyOptions(urgencies)).toEqual([{ value: 'u1', label: 'Urgent' }, { value: 'u2', label: 'Standard' }]);
    expect(urgencyOptions([])).toEqual([]);
  });
});
