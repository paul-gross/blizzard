import type { Meta, StoryObj } from '@storybook/angular-vite';

import { KitCloseButton } from './kit-close-button';

const meta: Meta<KitCloseButton> = {
  title: 'Kit/Close button',
  component: KitCloseButton,
  args: { ariaLabel: 'Close', testid: null },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-close-button [ariaLabel]="ariaLabel" [testid]="testid" />`,
  }),
};
export default meta;

type Story = StoryObj<KitCloseButton>;

export const Default: Story = {};
export const CustomLabel: Story = { args: { ariaLabel: 'Close the delivery drawer' } };
