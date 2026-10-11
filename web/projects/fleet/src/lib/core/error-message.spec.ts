import { errorMessage } from './error-message';

describe('errorMessage', () => {
  it('reads a string detail', () => {
    expect(errorMessage({ detail: 'stale revision' }, 'failed')).toBe('stale revision');
  });

  it("names each field of a 422's list-shaped detail", () => {
    const error = {
      detail: [
        {
          loc: ['body', 'owner'],
          msg: 'must not be blank',
          type: 'value_error',
        },
        {
          loc: ['body', 'secret_name'],
          msg: 'unknown secret',
          type: 'value_error',
        },
      ],
    };
    expect(errorMessage(error, 'failed')).toBe('owner: must not be blank; secret_name: unknown secret');
  });

  it("reads a coded refusal's message", () => {
    const error = { detail: { code: 'source_not_linked', message: 'The source is not linked to this project.' } };
    expect(errorMessage(error, 'failed')).toBe('The source is not linked to this project.');
  });

  it('names every project a project_required refusal asks to choose from', () => {
    const error = {
      detail: {
        code: 'project_required',
        message: 'This tenant holds several projects; name one.',
        projects: [
          { project_id: 'proj_a', slug: 'blizzard', name: 'Blizzard' },
          { project_id: 'proj_b', slug: 'winter', name: 'winter' },
        ],
      },
    };
    expect(errorMessage(error, 'failed')).toBe(
      'This tenant holds several projects; name one. Projects: Blizzard (blizzard), winter.',
    );
  });

  it('falls back when no body can be read', () => {
    expect(errorMessage(undefined, 'failed')).toBe('failed');
    expect(errorMessage({ detail: [] }, 'failed')).toBe('failed');
    expect(errorMessage({ detail: [{ nope: 1 }] }, 'failed')).toBe('failed');
    expect(errorMessage({ detail: { code: 'project_required' } }, 'failed')).toBe('failed');
  });
});
