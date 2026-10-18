import type { Meta, StoryObj } from '@storybook/angular-vite';

import { KitButton } from './kit-button';

const meta: Meta<KitButton> = {
  title: 'Kit/Button',
  component: KitButton,
  args: { variant: 'default', size: 'default', disabled: false, ariaLabel: null, title: null, testid: null },
  argTypes: {
    variant: { control: 'inline-radio', options: ['default', 'primary', 'danger'] },
    size: { control: 'inline-radio', options: ['default', 'cta'] },
  },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-button [variant]="variant" [size]="size" [disabled]="disabled" [ariaLabel]="ariaLabel" [title]="title" [testid]="testid">Retry chunk</fleet-kit-button>`,
  }),
};
export default meta;

type Story = StoryObj<KitButton>;

export const Default: Story = {};
export const Primary: Story = { args: { variant: 'primary' } };
export const Danger: Story = { args: { variant: 'danger' } };
export const CallToAction: Story = { args: { variant: 'primary', size: 'cta' } };
export const Disabled: Story = { args: { disabled: true } };
