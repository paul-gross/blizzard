import type { Meta, StoryObj } from '@storybook/angular-vite';
import type { MemberView } from 'fleet';
import { memberView } from 'fleet/testing';
import { fn } from 'storybook/test';
import { MembersTable } from './members-table';

const meta: Meta<MembersTable> = {
  title: 'Hub admin/Members table',
  component: MembersTable,
  args: {
    state: 'ready',
    members: [memberView({ username: 'grace', display_name: 'Grace', role: 'guest', assignable_roles: ['guest', 'contributor'], revocable: true })],
    currentUserId: 'usr_self',
    changeRole: fn(),
    revoke: fn(),
  },
};
export default meta;
type Story = StoryObj<MembersTable>;
export const Ready: Story = {};
const GUEST: Partial<MemberView> = { role: 'guest', assignable_roles: ['guest', 'contributor'], revocable: true };

/** Several rows: a static self row, an actionable row, and a row whose change is in flight (its controls disabled). */
export const PendingRow: Story = {
  args: {
    members: [
      memberView({ user_id: 'usr_self', username: 'ada', display_name: 'Ada', role: 'admin', assignable_roles: [], revocable: false }),
      memberView({ user_id: 'usr_grace', username: 'grace', display_name: 'Grace', ...GUEST }),
      memberView({ user_id: 'usr_otto', username: 'otto', display_name: 'Otto', ...GUEST }),
    ],
    pendingUserIds: ['usr_otto'],
  },
};

/** A table with no actions: the actor's own row and a row whose role they cannot change — no select, no revoke. */
export const NoActions: Story = {
  args: {
    members: [
      memberView({ user_id: 'usr_self', username: 'ada', display_name: 'Ada', role: 'admin', assignable_roles: [], revocable: false }),
      memberView({ user_id: 'usr_last', username: 'lin', display_name: 'Lin', role: 'admin', assignable_roles: ['admin'], revocable: false }),
    ],
  },
};
export const Loading: Story = { args: { state: 'loading' } };
export const Error: Story = { args: { state: 'error' } };
export const Empty: Story = { args: { state: 'empty', members: [] } };
export const PhoneSmall: Story = { globals: { viewport: { value: 'phone-sm', isRotated: false } } };
