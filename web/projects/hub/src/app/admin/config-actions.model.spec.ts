import { configActions, failureFor, SECRET_IN_USE_NOTE } from './config-actions.model';

describe('configActions', () => {
  const live = { retired: false };

  it('builds no control on a phone, without config:edit, with no record, or for a built-in record', () => {
    expect(configActions('mobile', true, live)).toBeNull();
    expect(configActions('desktop', false, live)).toBeNull();
    expect(configActions('desktop', true, null)).toBeNull();
    expect(configActions('desktop', true, { built_in: true })).toBeNull();
  });

  it('offers edit and retire on a live record, enable on a retired one', () => {
    expect(configActions('desktop', true, live)).toEqual({
      edit: true,
      replace: false,
      lifecycle: 'retire',
      retireBlocked: null,
    });
    expect(configActions('desktop', true, { retired: true })?.lifecycle).toBe('enable');
  });

  it('offers replace instead of edit for a secret, and holds Retire back while referenced', () => {
    const actions = configActions('desktop', true, live, {
      replace: true,
      referenceCount: 2,
    });
    expect(actions).toMatchObject({
      edit: false,
      replace: true,
      retireBlocked: SECRET_IN_USE_NOTE,
    });
  });

  it('does not hold Enable back for a retired secret', () => {
    expect(
      configActions('desktop', true, { retired: true }, { replace: true, referenceCount: 1 })?.retireBlocked,
    ).toBeNull();
  });
});

describe('failureFor', () => {
  it('shows a refusal only for the record it was raised on', () => {
    const failure = { key: 'a', message: 'in use' };
    expect(failureFor(failure, 'a')).toBe('in use');
    expect(failureFor(failure, 'b')).toBeNull();
    expect(failureFor(null, 'a')).toBeNull();
  });
});
