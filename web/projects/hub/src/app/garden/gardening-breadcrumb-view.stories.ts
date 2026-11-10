import type { Meta, StoryObj } from '@storybook/angular-vite';

import { GardeningBreadcrumbView } from './gardening-breadcrumb-view';

const meta: Meta<GardeningBreadcrumbView> = {
  title: 'Hub garden/Breadcrumb',
  component: GardeningBreadcrumbView,
  args: {
    doorwayHref: '/gardening',
    projectLabel: 'Blizzard',
    checkedSlug: 'blizzard',
    surfaceLabel: 'Proposals',
    choices: [
      { slug: 'blizzard', label: 'Blizzard', waiting: 2 },
      { slug: 'winter', label: 'Winter', waiting: 0 },
      { slug: null, label: 'All projects', waiting: null },
    ],
  },
};
export default meta;
type Story = StoryObj<GardeningBreadcrumbView>;
export const Loaded: Story = {};
export const WaitingUnread: Story = {
  args: { choices: [{ slug: 'blizzard', label: 'Blizzard', waiting: null }, { slug: null, label: 'All projects', waiting: null }] },
};
export const Narrow: Story = { globals: { viewport: { value: 'phone-sm', isRotated: false } } };
