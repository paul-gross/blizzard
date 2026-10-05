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

  it('falls back when no body can be read', () => {
    expect(errorMessage(undefined, 'failed')).toBe('failed');
    expect(errorMessage({ detail: [] }, 'failed')).toBe('failed');
    expect(errorMessage({ detail: [{ nope: 1 }] }, 'failed')).toBe('failed');
  });
});
