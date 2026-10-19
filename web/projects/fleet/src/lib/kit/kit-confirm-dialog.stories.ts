import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { KitConfirmDialog } from './kit-confirm-dialog';

const meta: Meta<KitConfirmDialog> = {
  title: 'Kit/Confirm dialog',
  component: KitConfirmDialog,
  args: {
    open: true,
    heading: 'Stop the chunk',
    message: 'The running node is interrupted and its worktree is kept.',
    confirmLabel: 'Stop',
    variant: 'primary',
    testid: 'confirm-dialog',
    confirmed: fn(),
    cancelled: fn(),
  },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-confirm-dialog [open]="open" [heading]="heading" [message]="message" [confirmLabel]="confirmLabel" [variant]="variant" [testid]="testid" (confirmed)="confirmed()" (cancelled)="cancelled()" />`,
  }),
};
export default meta;

type Story = StoryObj<KitConfirmDialog>;

export const Default: Story = {};
export const Danger: Story = {
  args: {
    heading: 'Delete the project',
    message: 'This cannot be undone.',
    confirmLabel: 'Delete',
    variant: 'danger',
  },
};
export const Closed: Story = { args: { open: false } };
export const LongMessage: Story = {
  args: {
    message:
      'The running node is interrupted mid-step and its worktree is kept on the runner host.\n\nAny uncommitted changes stay in the worktree until the chunk is resumed or discarded, and the lane it held is released for the next chunk in the queue.',
  },
};
export const Phone: Story = {
  args: LongMessage.args,
  globals: { viewport: { value: 'phone', isRotated: false } },
};
export const PhoneSmall: Story = {
  args: LongMessage.args,
  globals: { viewport: { value: 'phone-sm', isRotated: false } },
};
