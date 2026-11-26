import { moduleMetadata, type Meta, type StoryObj } from '@storybook/angular-vite';

import { ProposalAcceptDialogStoryHost } from './gardening-proposal-accept-dialog-view.story-host';

const meta: Meta = {
  title: 'Hub garden/Accept proposal',
  decorators: [moduleMetadata({ imports: [ProposalAcceptDialogStoryHost] })],
};
export default meta;

type Story = StoryObj;

const host = (attrs = ''): Story => ({
  render: (args) => ({
    props: args,
    template: `<app-story-proposal-accept-dialog [urgencyOptions]="urgencyOptions" [draft]="draft" ${attrs} />`,
  }),
  args: {
    urgencyOptions: [
      { value: '', label: 'Tenant default (Normal)' },
      { value: 'normal', label: 'Normal' },
      { value: 'urgent', label: 'Urgent' },
    ],
    draft: {},
  },
});

export const Default: Story = host();
export const Phone: Story = { ...Default, globals: { viewport: { value: 'phone-sm', isRotated: false } } };
export const Decline: Story = { ...host(), args: { ...Default.args, draft: { mode: 'decline' } } };
export const ReasonRefused: Story = {
  ...host('reasonError="reason must not be blank"'),
  args: { ...Default.args, draft: { mode: 'decline', reason: 'already tracked' } },
};
export const SubmitError: Story = host(`formError="garden proposal gp_1 already carries a closure"`);
export const Submitting: Story = host('[submitting]="true"');
