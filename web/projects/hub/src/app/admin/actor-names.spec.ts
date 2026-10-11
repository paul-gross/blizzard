import type { MemberView } from 'fleet';

import { actorNameOf, RAW_ACTOR } from './actor-names';

function member(user_id: string, display_name: string, username: string): MemberView {
  return {
    user_id,
    display_name,
    username,
    email: null,
    hub_admin: false,
    role: 'guest',
    created_at: '2026-10-01T00:00:00Z',
  };
}

describe('actorNameOf', () => {
  it("names an actor by the member's display name, else the username", () => {
    const name = actorNameOf([member('usr_1', 'Paul Gross', 'pgross'), member('usr_2', '', 'ada')]);
    expect(name('usr_1')).toBe('Paul Gross');
    expect(name('usr_2')).toBe('ada');
  });

  it('keeps an actor no member holds as recorded', () => {
    const name = actorNameOf([member('usr_1', 'Paul Gross', 'pgross')]);
    expect(name('usr_gone')).toBe('usr_gone');
    expect(name('migration')).toBe('migration');
  });

  it('keeps every actor as recorded before the members are read', () => {
    expect(actorNameOf(undefined)).toBe(RAW_ACTOR);
    expect(actorNameOf([])('usr_1')).toBe('usr_1');
  });
});
