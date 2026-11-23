import { lensNoteText } from './lens-note.model';
import { project } from './project.builders';

const WINTER = project({ project_id: 'proj_w', slug: 'winter', name: 'Winter', color: 'teal' });

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
