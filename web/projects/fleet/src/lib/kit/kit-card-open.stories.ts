import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { KitCardOpen } from './kit-card-open';

const meta: Meta<KitCardOpen> = {
  title: 'Kit/Card open',
  component: KitCardOpen,
  args: { ariaLabel: 'Open chunk ch_1', current: false, testid: null, title: null, opened: fn() },
  render: (args) => ({
    props: args,
    template: `
      <div style="width: 320px; padding: 8px; border: 1px solid var(--line)">
        <fleet-kit-card-open [ariaLabel]="ariaLabel" [current]="current" [testid]="testid" [title]="title" (opened)="opened()">Card body</fleet-kit-card-open>
      </div>`,
  }),
};
export default meta;

type Story = StoryObj<KitCardOpen>;

export const Default: Story = {};
export const Current: Story = { args: { current: true } };
