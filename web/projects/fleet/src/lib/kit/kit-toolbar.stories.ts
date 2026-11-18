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
  render: () => ({ props: { invoked: fn() }, template: `
    <button type="button">Before toolbar</button>
    <div fleetKitToolbar aria-label="Actions" style="max-width: 16rem">
      <fleet-kit-chip fleetKitToolbarWidget (invoked)="invoked()">Accept</fleet-kit-chip>
      <fleet-kit-chip fleetKitToolbarWidget (invoked)="invoked()">Decline</fleet-kit-chip>
      <fleet-kit-chip fleetKitToolbarWidget (invoked)="invoked()">Defer</fleet-kit-chip>
      <fleet-kit-chip fleetKitToolbarWidget (invoked)="invoked()">Reassign</fleet-kit-chip>
      <fleet-kit-chip fleetKitToolbarWidget [disabled]="true">Unavailable</fleet-kit-chip>
      <fleet-kit-chip fleetKitToolbarWidget (invoked)="invoked()">Archive</fleet-kit-chip>
    </div>
    <button type="button">After toolbar</button>
  ` }),
};
export default meta;
type Story = StoryObj<KitToolbar>;
export const Actions: Story = {};
