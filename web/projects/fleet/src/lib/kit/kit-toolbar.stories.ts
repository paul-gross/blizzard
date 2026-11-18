import type { Meta, StoryObj } from '@storybook/angular-vite';
import { moduleMetadata } from '@storybook/angular-vite';
import { fn } from 'storybook/test';
import { KitToolbar, KitToolbarWidget } from './kit-toolbar';
import { KitChip } from './kit-chips';

const meta: Meta<KitToolbar> = {
  title: 'Kit/Toolbar',
  component: KitToolbar,
  decorators: [moduleMetadata({ imports: [KitToolbar, KitToolbarWidget, KitChip] })],
  args: {},
  render: () => ({ props: { invoked: fn() }, template: `<div fleetKitToolbar aria-label="Actions"><fleet-kit-chip fleetKitToolbarWidget (invoked)="invoked()">Accept</fleet-kit-chip> <fleet-kit-chip fleetKitToolbarWidget (invoked)="invoked()">Decline</fleet-kit-chip> <fleet-kit-chip fleetKitToolbarWidget [disabled]="true">Unavailable</fleet-kit-chip></div>` }),
};
export default meta;
type Story = StoryObj<KitToolbar>;
export const Actions: Story = {};
