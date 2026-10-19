import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { KitTabs, type KitTabOption } from './kit-tabs';

const OPTIONS: readonly KitTabOption[] = [
  { value: 'general', label: 'General' },
  { value: 'artifacts', label: 'Artifacts' },
  { value: 'transcripts', label: 'Transcripts' },
];

const COUNTED: readonly KitTabOption[] = [
  { value: 'general', label: 'General' },
  { value: 'artifacts', label: 'Artifacts', count: 12 },
  { value: 'transcripts', label: 'Transcripts', count: 0 },
];

const MANY: readonly KitTabOption[] = Array.from({ length: 14 }, (_, i) => ({
  value: `tab-${i}`,
  label: `Node-step ${i + 1}`,
}));

const meta: Meta<KitTabs> = {
  title: 'Kit/Tabs',
  component: KitTabs,
  args: {
    options: OPTIONS,
    activeValue: 'general',
    variant: 'top',
    choose: fn(),
  },
  argTypes: { variant: { control: 'inline-radio', options: ['top', 'sub'] } },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-tabs [options]="options" [activeValue]="activeValue" [variant]="variant" (choose)="choose($event)" />`,
  }),
};
export default meta;

type Story = StoryObj<KitTabs>;

export const Top: Story = {};
export const Sub: Story = { args: { variant: 'sub' } };
export const WithCounts: Story = { args: { options: COUNTED } };
export const NoActiveTab: Story = { args: { activeValue: null } };
export const Overflowing: Story = {
  args: { options: MANY, activeValue: 'tab-0' },
};
export const NarrowPhone: Story = {
  args: { options: COUNTED },
  globals: { viewport: { value: 'phone', isRotated: false } },
};
export const NarrowPhoneSmall: Story = {
  args: { options: COUNTED },
  globals: { viewport: { value: 'phone-sm', isRotated: false } },
};
