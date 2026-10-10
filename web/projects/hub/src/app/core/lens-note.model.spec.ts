import type { ProjectView } from 'fleet';

import { lensNoteText } from './lens-note.model';

const WINTER: ProjectView = {
  project_id: 'proj_w',
  slug: 'winter',
  name: 'Winter',
  description: '',
  revision: 1,
  created_at: '2026-10-01T00:00:00Z',
};

describe('lensNoteText', () => {
  it('names the lensed project and says the view is the whole tenant', () => {
    expect(lensNoteText('Secrets', WINTER)).toBe(
      'Secrets belong to the whole tenant: the lens on Winter does not narrow this view.',
    );
  });

  it('says nothing on All', () => {
    expect(lensNoteText('Secrets', null)).toBeNull();
  });
});
