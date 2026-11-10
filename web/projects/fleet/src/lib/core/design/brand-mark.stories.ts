import type { Meta, StoryObj } from '@storybook/angular-vite';

import { BrandMark } from './brand-mark';

const meta: Meta<BrandMark> = {
  title: 'Design/Brand mark',
  component: BrandMark,
  args: { size: 28 },
};
export default meta;

type Story = StoryObj<BrandMark>;

export const Default: Story = {};
export const Large: Story = { args: { size: 96 } };
export const Small: Story = { args: { size: 16 } };
