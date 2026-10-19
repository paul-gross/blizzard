import type { Meta, StoryObj } from '@storybook/angular-vite';

import { KitAvatar } from './kit-avatar';

const meta: Meta<KitAvatar> = {
  title: 'Kit/Avatar',
  component: KitAvatar,
  render: () => ({
    template: `<fleet-kit-avatar />`,
  }),
};
export default meta;

type Story = StoryObj<KitAvatar>;

export const Default: Story = {};
