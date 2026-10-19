import {
  moduleMetadata,
  type Meta,
  type StoryObj,
} from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { KitMenuPanel } from './kit-menu';
import { KitMenuItemRadio } from './kit-menu-item';

/** The host-directive inputs and outputs the class itself does not declare. */
type Args = KitMenuItemRadio & {
  checked: boolean;
  disabled: boolean;
  triggered: () => void;
};

const meta: Meta<Args> = {
  title: 'Kit/Menu radio item',
  component: KitMenuItemRadio,
  decorators: [moduleMetadata({ imports: [KitMenuPanel] })],
  args: { testid: null, checked: true, disabled: false, triggered: fn() },
  render: (args) => ({
    props: args,
    template: `
      <fleet-kit-menu-panel style="width: 240px">
        <fleet-kit-menu-item-radio [testid]="testid" [checked]="checked" [disabled]="disabled" (triggered)="triggered()">r-claude</fleet-kit-menu-item-radio>
        <fleet-kit-menu-item-radio>r-chatgpt</fleet-kit-menu-item-radio>
      </fleet-kit-menu-panel>`,
  }),
};
export default meta;

type Story = StoryObj<Args>;

export const Checked: Story = {};
export const Unchecked: Story = { args: { checked: false } };
export const Disabled: Story = { args: { disabled: true } };
export const NameAndId: Story = {
  render: (args) => ({
    props: args,
    template: `
      <fleet-kit-menu-panel style="width: 280px">
        <fleet-kit-menu-item-radio [testid]="testid" [checked]="checked" [disabled]="disabled" (triggered)="triggered()">r-claude <code>rn_01M3MY1NGQR8WE1ZBRKNREFNE4</code></fleet-kit-menu-item-radio>
        <fleet-kit-menu-item-radio>r-chatgpt <code>rn_01M3MY1NGQR8WE1ZBRKNREFNE5</code></fleet-kit-menu-item-radio>
      </fleet-kit-menu-panel>`,
  }),
};
