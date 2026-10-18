import type { Meta, StoryObj } from '@storybook/angular-vite';

import { KitPanel } from './kit-panel';

const meta: Meta<KitPanel> = {
  title: 'Kit/Panel',
  component: KitPanel,
  args: { label: 'Runners', count: 3, countTestid: null, accent: null, bodyScroll: true },
  render: (args) => ({
    props: args,
    template: `
      <fleet-kit-panel [label]="label" [count]="count" [countTestid]="countTestid" [accent]="accent" [bodyScroll]="bodyScroll">
        <ul style="margin: 0; padding: 8px 12px; list-style: none">
          <li>r-claude — Claude Code, 2 of 4 slots busy</li>
          <li>r-chatgpt — OpenCode, 1 of 4 slots busy</li>
          <li>xps — host, heartbeat steady</li>
        </ul>
      </fleet-kit-panel>`,
  }),
};
export default meta;

type Story = StoryObj<KitPanel>;

export const Labelled: Story = {};
export const WithoutCount: Story = { args: { count: null } };
export const Accented: Story = { args: { accent: 'var(--red)' } };
export const Unlabelled: Story = { args: { label: null, count: null } };
