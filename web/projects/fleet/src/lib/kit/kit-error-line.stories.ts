import type { Meta, StoryObj } from '@storybook/angular-vite';

import { KitErrorLine } from './kit-error-line';

const meta: Meta<KitErrorLine> = {
  title: 'Kit/Error line',
  component: KitErrorLine,
  args: { message: 'The record changed since you opened it.', testid: null },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-error-line [message]="message" [testid]="testid" />`,
  }),
};
export default meta;

type Story = StoryObj<KitErrorLine>;

export const Default: Story = {};
export const Empty: Story = { args: { message: null } };
export const LongUnbroken: Story = {
  args: {
    message:
      'Request failed: https://hub.example/api/projects/prj_01HZXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX/links/work-sources',
  },
};
export const NarrowPhoneSmall: Story = {
  args: LongUnbroken.args,
  globals: { viewport: { value: 'phone-sm', isRotated: false } },
};
