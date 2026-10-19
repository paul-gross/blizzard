import {
  moduleMetadata,
  type Meta,
  type StoryObj,
} from '@storybook/angular-vite';

import { KitMenuPanel } from './kit-menu';
import { KitMenuItem, KitMenuItemRadio } from './kit-menu-item';

const meta: Meta<KitMenuPanel> = {
  title: 'Kit/Menu panel',
  component: KitMenuPanel,
  decorators: [moduleMetadata({ imports: [KitMenuItem, KitMenuItemRadio] })],
  args: { testid: null },
  render: (args) => ({
    props: args,
    template: `
      <fleet-kit-menu-panel [testid]="testid" style="width: 240px">
        <fleet-kit-menu-item>Retry chunk</fleet-kit-menu-item>
        <fleet-kit-menu-item>Stop chunk</fleet-kit-menu-item>
        <hr />
        <fleet-kit-menu-item>Open in board</fleet-kit-menu-item>
      </fleet-kit-menu-panel>`,
  }),
};
export default meta;

type Story = StoryObj<KitMenuPanel>;

export const PlainItemsWithDivider: Story = {};
export const MixedItems: Story = {
  render: (args) => ({
    props: args,
    template: `
      <fleet-kit-menu-panel [testid]="testid" style="width: 240px">
        <fleet-kit-menu-item>Retry chunk</fleet-kit-menu-item>
        <hr />
        <fleet-kit-menu-item-radio [checked]="true">r-claude</fleet-kit-menu-item-radio>
        <fleet-kit-menu-item-radio>r-chatgpt</fleet-kit-menu-item-radio>
        <hr />
        <fleet-kit-menu-item [submenu]="true">Move to</fleet-kit-menu-item>
      </fleet-kit-menu-panel>`,
  }),
};
export const LongItemLabels: Story = {
  render: (args) => ({
    props: args,
    template: `
      <fleet-kit-menu-panel [testid]="testid" style="width: 240px">
        <fleet-kit-menu-item>Retry the chunk from its last successful node-step on a fresh lease</fleet-kit-menu-item>
        <fleet-kit-menu-item>Stop the chunk and release every environment it holds</fleet-kit-menu-item>
      </fleet-kit-menu-panel>`,
  }),
};
