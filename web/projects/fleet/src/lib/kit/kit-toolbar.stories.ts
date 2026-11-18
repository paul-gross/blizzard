import type { Meta, StoryObj } from '@storybook/angular-vite';
import { moduleMetadata } from '@storybook/angular-vite';
import { fn } from 'storybook/test';
import { KitToolbar, KitToolbarWidget } from './kit-toolbar';
import { KitChip } from './kit-chips';

type ToolbarArgs = KitToolbar & { disabled: boolean; orientation: 'horizontal' | 'vertical'; invoked: () => void };

const meta: Meta<ToolbarArgs> = {
  title: 'Kit/Toolbar',
  component: KitToolbar,
  decorators: [moduleMetadata({ imports: [KitToolbar, KitToolbarWidget, KitChip] })],
  args: { disabled: false, orientation: 'horizontal', invoked: fn() },
  argTypes: { orientation: { control: 'select', options: ['horizontal', 'vertical'] } },
  render: (args) => ({ props: args, template: `
    <button type="button">Before toolbar</button>
    <div fleetKitToolbar [disabled]="disabled" [orientation]="orientation" aria-label="Actions" style="max-width: 16rem">
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
type Story = StoryObj<ToolbarArgs>;
export const Actions: Story = {};
export const Disabled: Story = { args: { disabled: true } };
export const Vertical: Story = { args: { orientation: 'vertical' } };
