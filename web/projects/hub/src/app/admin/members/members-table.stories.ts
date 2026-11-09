import type { Meta, StoryObj } from '@storybook/angular-vite';
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
export const Loading: Story = { args: { state: 'loading' } };
export const Error: Story = { args: { state: 'error' } };
export const Empty: Story = { args: { state: 'empty', members: [] } };
export const PhoneSmall: Story = { globals: { viewport: { value: 'phone-sm', isRotated: false } } };
