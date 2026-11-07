import type { ProjectRequiredBody } from '../api/hub';
import type { PauseChunkRunnersRunnerIdApiChunksChunkIdPausePostErrors } from '../api/runner';
import { errorMessage } from './error-message';

describe('errorMessage', () => {
  it('accepts both generated proxy 422 variants', () => {
    const problem: PauseChunkRunnersRunnerIdApiChunksChunkIdPausePostErrors[422] = {
      type: 'urn:example:refusal', detail: 'refused',
    };
    const validation: PauseChunkRunnersRunnerIdApiChunksChunkIdPausePostErrors[422] = {
      detail: [{ loc: ['body'], msg: 'invalid', type: 'value_error' }],
    };
    expect(errorMessage(problem, 'failed')).toBe('refused');
    expect(errorMessage(validation, 'failed')).toContain('invalid');
  });
  it("reads a problem's string detail whatever its type", () => {
    const error = {
      type: 'urn:blizzard:problem:chunk-not-found',
      title: 'Chunk not found',
      detail: 'no such chunk ch_1',
    };
    expect(errorMessage(error, 'failed')).toBe('no such chunk ch_1');
  });

  it('renders the title of a problem with no detail', () => {
    expect(
      errorMessage(
        { type: 'urn:blizzard:problem:flag-off', title: 'Not available yet' },
        'failed',
      ),
    ).toBe('Not available yet');
  });

  it('reads a problem of a type it does not know by its message', () => {
    const error = {
      type: 'urn:blizzard:problem:from-the-future',
      title: 'T',
      detail: 'words',
    };
    expect(errorMessage(error, 'failed')).toBe('words');
  });

  it('reads the message of a typed problem whose detail is the legacy object', () => {
    const error = {
      type: 'urn:blizzard:problem:flag-off',
      title: 'T',
      detail: { code: 'flag_off', message: 'off' },
    };
    expect(errorMessage(error, 'failed')).toBe('off');
  });

  it('falls back on input that is not an object', () => {
    expect(errorMessage('boom', 'failed')).toBe('failed');
    expect(errorMessage(null, 'failed')).toBe('failed');
    expect(errorMessage(['x'], 'failed')).toBe('failed');
  });

  it('reads a string detail', () => {
    expect(errorMessage({ detail: 'stale revision' }, 'failed')).toBe(
      'stale revision',
    );
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
    expect(errorMessage(error, 'failed')).toBe(
      'owner: must not be blank; secret_name: unknown secret',
    );
  });

  it("reads a coded refusal's message", () => {
    const error = {
      detail: {
        code: 'source_not_linked',
        message: 'The source is not linked to this project.',
      },
    };
    expect(errorMessage(error, 'failed')).toBe(
      'The source is not linked to this project.',
    );
  });

  it('names every project a project_required refusal asks to choose from', () => {
    const detail: ProjectRequiredBody = {
      code: 'project_required',
      message: 'This tenant holds several projects; name one.',
      projects: [
        { project_id: 'proj_a', slug: 'blizzard', name: 'Blizzard' },
        { project_id: 'proj_b', slug: 'winter', name: 'winter' },
      ],
    };
    const error = { detail };
    expect(errorMessage(error, 'failed')).toBe(
      'This tenant holds several projects; name one. Projects: Blizzard (blizzard), winter.',
    );
  });

  it('falls back when no body can be read', () => {
    expect(errorMessage(undefined, 'failed')).toBe('failed');
    expect(errorMessage({ detail: [] }, 'failed')).toBe('failed');
    expect(errorMessage({ detail: [{ nope: 1 }] }, 'failed')).toBe('failed');
    expect(errorMessage({ detail: { projects: [] } }, 'failed')).toBe('failed');
  });
});
