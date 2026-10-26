import { test as plain } from '@playwright/test';
import { expect, test } from '../fixtures';
import { requireAuth } from '../people';

plain('the refusal on a hub with authentication off names the auth profile', () => {
  expect(() => requireAuth({ auth: undefined })).toThrow(/`auth` profile/);
  expect(() => requireAuth({ auth: { providers: ['stub'] } })).not.toThrow();
});

test.describe('user on the default profile', () => {
  test.use({ isolation: 'common' });
  test.fail(true, 'user needs the auth profile, and fails before the test starts, naming it');
  test('fails before the test starts', ({ user }) => {
    void user;
  });
});

test.describe('people on the default profile', () => {
  test.use({ isolation: 'common' });
  test.fail(true, 'people needs the auth profile, and fails before the test starts, naming it');
  test('fails before the test starts', ({ people }) => {
    void people;
  });
});
