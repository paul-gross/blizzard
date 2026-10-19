import {
  moduleMetadata,
  type Meta,
  type StoryObj,
} from '@storybook/angular-vite';

import { KitMenuPanel } from './kit-menu';
import { KitMenuItem, KitMenuItemSubtitle } from './kit-menu-item';

const meta: Meta<KitMenuItemSubtitle> = {
  title: 'Kit/Menu item subtitle',
  component: KitMenuItemSubtitle,
  decorators: [moduleMetadata({ imports: [KitMenuPanel, KitMenuItem] })],
  render: () => ({
    template: `
      <fleet-kit-menu-panel style="width: 240px">
        <fleet-kit-menu-item>Retry chunk<span fleetKitMenuItemSubtitle>from the last node-step</span></fleet-kit-menu-item>
      </fleet-kit-menu-panel>`,
  }),
};
export default meta;

type Story = StoryObj<KitMenuItemSubtitle>;

export const ShortSubtitle: Story = {};
export const LongSubtitle: Story = {
  render: () => ({
    template: `
      <fleet-kit-menu-panel style="width: 240px">
        <fleet-kit-menu-item>Retry chunk<span fleetKitMenuItemSubtitle>from the last successful node-step, on a fresh lease, in a clean environment</span></fleet-kit-menu-item>
      </fleet-kit-menu-panel>`,
  }),
};
