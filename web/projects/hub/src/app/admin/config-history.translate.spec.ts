import { describe, expect, it } from 'vitest';
import { configChangeView } from 'fleet/testing';
import { toConfigChange } from './config-history.translate';

describe('history projection', () => {
  it('retains revision, actor, and raw and labelled diff values', () => {
    const fields = { id: 5, revision: 3, actor: 'a', actor_name: null, door: 'cli', op: 'edit', recorded_at: 'now',
      diff: [{ field: 'slug', old: 'before', new: 'after', old_label: null, new_label: 'After' }] };
    expect(toConfigChange(configChangeView(fields))).toEqual(fields);
  });
});
