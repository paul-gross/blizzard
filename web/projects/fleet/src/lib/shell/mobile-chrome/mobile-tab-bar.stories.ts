import type { Meta, StoryObj } from '@storybook/angular-vite';

import { MobileTabBar, type MobileTabItem } from './mobile-tab-bar';

const ITEMS: readonly MobileTabItem[] = [
  { testid: 'tab-board', label: 'Board', active: true },
  {
    testid: 'tab-asks',
    label: 'Asks',
    badge: 3,
    badgeTestid: 'tab-asks-badge',
  },
  { testid: 'tab-fleet', label: 'Fleet' },
];

const meta: Meta<MobileTabBar> = {
  title: 'Shell/Mobile tab bar',
  component: MobileTabBar,
  args: { items: ITEMS, testid: 'mobile-tab-bar' },
  globals: { viewport: { value: 'phone', isRotated: false } },
};
export default meta;

type Story = StoryObj<MobileTabBar>;

export const Phone: Story = {};
export const PhoneSmall: Story = {
  globals: { viewport: { value: 'phone-sm', isRotated: false } },
};
/** The runner's four-tab strip, the widest the bar is handed. */
export const FourTabsPhoneSmall: Story = {
  args: {
    items: [
      { testid: 'tab-board', label: 'Board', active: true },
      {
        testid: 'tab-asks',
        label: 'Asks',
        badge: 12,
        badgeTestid: 'tab-asks-badge',
      },
      { testid: 'tab-transcripts', label: 'Transcripts' },
      { testid: 'tab-events', label: 'Events', inert: true },
    ],
  },
  globals: { viewport: { value: 'phone-sm', isRotated: false } },
};
