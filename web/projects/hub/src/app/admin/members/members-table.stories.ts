import type { Meta, StoryObj } from '@storybook/angular-vite';
import type { Member } from './members.domain';
import { member } from './members.builders';
import { fn } from 'storybook/test';
import { MembersTable } from './members-table';

const meta: Meta<MembersTable> = {
  title: 'Hub admin/Members table',
  component: MembersTable,
  args: {
    state: 'ready',
    members: [member({ username: 'grace', displayName: 'Grace', role: 'guest', assignableRoles: ['guest', 'contributor'], revocable: true })],
    currentUserId: 'usr_self',
    changeRole: fn(),
    revoke: fn(),
  },
};
export default meta;
type Story = StoryObj<MembersTable>;
export const Ready: Story = {};
const GUEST: Partial<Member> = { role: 'guest', assignableRoles: ['guest', 'contributor'], revocable: true };

/** Several rows: a static self row, an actionable row, and a row whose change is in flight (its controls disabled). */
export const PendingRow: Story = {
  args: {
    members: [
      member({ userId: 'usr_self', username: 'ada', displayName: 'Ada', role: 'admin', assignableRoles: [], revocable: false }),
      member({ userId: 'usr_grace', username: 'grace', displayName: 'Grace', ...GUEST }),
      member({ userId: 'usr_otto', username: 'otto', displayName: 'Otto', ...GUEST }),
    ],
    pendingUserIds: ['usr_otto'],
  },
};

/** A table with no actions: the actor's own row and a row whose role they cannot change — no select, no revoke. */
export const NoActions: Story = {
  args: {
    members: [
      member({ userId: 'usr_self', username: 'ada', displayName: 'Ada', role: 'admin', assignableRoles: [], revocable: false }),
      member({ userId: 'usr_last', username: 'lin', displayName: 'Lin', role: 'admin', assignableRoles: ['admin'], revocable: false }),
    ],
  },
};
export const Loading: Story = { args: { state: 'loading' } };
export const Error: Story = { args: { state: 'error' } };
export const Empty: Story = { args: { state: 'empty', members: [] } };
export const PhoneSmall: Story = { globals: { viewport: { value: 'phone-sm', isRotated: false } } };
