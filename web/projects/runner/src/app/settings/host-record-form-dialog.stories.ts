import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { HostRecordFormDialogHost } from './host-record-form-dialog.story-host';

const meta: Meta<HostRecordFormDialogHost> = {
  title: 'Runner/Host record form dialog',
  component: HostRecordFormDialogHost,
  args: {
    kind: 'workspace',
    mode: 'create',
    heading: 'Create workspace',
    draft: {},
    fieldErrors: {},
    formError: null,
    submitting: false,
    save: fn(),
    closed: fn(),
  },
};
export default meta;
type Story = StoryObj<HostRecordFormDialogHost>;

export const CreateWorkspace: Story = {};
export const EditWorkspace: Story = {
  args: {
    mode: 'edit',
    heading: 'Edit workspace web',
    draft: {
      root: '/srv/web',
      envs: 'web-1, web-2',
      max_environments: '2',
      repo: 'blizzard https://github.com/example/blizzard.git\ncontext https://github.com/example/context.git',
      base_branch: 'master',
    },
  },
};
export const CreateSubscription: Story = { args: { kind: 'subscription', heading: 'Create subscription' } };
export const EditSubscription: Story = {
  args: { kind: 'subscription', mode: 'edit', heading: 'Edit subscription claude-max', draft: { name: 'Claude Max', sample_interval_seconds: '300' } },
};
export const RefusedFields: Story = {
  args: { draft: { id: 'web', max_environments: 'lots' }, fieldErrors: { max_environments: 'Enter a whole number.', root: 'root: no such directory' } },
};
export const RevisionConflict: Story = {
  args: { mode: 'edit', heading: 'Edit workspace web', draft: { root: '/srv/web' }, formError: 'the workspace record is at revision 4, not 3' },
};
export const Saving: Story = { args: { draft: { id: 'web' }, submitting: true } };
export const Phone: Story = { ...RefusedFields, globals: { viewport: { value: 'phone', isRotated: false } } };
export const PhoneSm: Story = { ...RefusedFields, globals: { viewport: { value: 'phone-sm', isRotated: false } } };
