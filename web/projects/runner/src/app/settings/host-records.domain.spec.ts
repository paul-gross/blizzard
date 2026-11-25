import { RECORD_FIELDS, parseRecordField, recordFieldError } from './host-records.domain';

const field = (kind: 'workspace' | 'subscription', name: string) => RECORD_FIELDS[kind].find((f) => f.name === name)!;

describe('host records domain', () => {
  it('reads each field type`s text as its value', () => {
    expect(parseRecordField(field('workspace', 'root'), '  /srv  ')).toEqual({ value: '/srv' });
    expect(parseRecordField(field('workspace', 'root'), '  ')).toEqual({ value: null });
    expect(parseRecordField(field('workspace', 'max_environments'), '4')).toEqual({ value: 4 });
    expect(parseRecordField(field('workspace', 'max_environments'), '4x')).toEqual({ error: 'Enter a whole number.' });
    expect(parseRecordField(field('workspace', 'envs'), 'a, b c')).toEqual({ value: ['a', 'b', 'c'] });
    expect(parseRecordField(field('workspace', 'repo'), 'a https://a\n\nb https://b')).toEqual({
      value: [
        { name: 'a', url: 'https://a' },
        { name: 'b', url: 'https://b' },
      ],
    });
    expect(parseRecordField(field('workspace', 'repo'), 'a')).toEqual({ error: 'Write each repository as its name and its URL.' });
    expect(parseRecordField(field('workspace', 'prompt'), ' line\n')).toEqual({ value: ' line\n' });
  });

  it('requires a create`s required keys, and refuses an edit returning a subscription`s name to its default', () => {
    expect(recordFieldError(field('workspace', 'id'), 'create', '')).toBe('Required.');
    expect(recordFieldError(field('subscription', 'name'), 'edit', '')).toBe('Required.');
    expect(recordFieldError(field('workspace', 'root'), 'edit', '')).toBeNull();
  });
});
