import type { Meta, StoryObj } from '@storybook/angular-vite';
import { moduleMetadata } from '@storybook/angular-vite';
import { fn } from 'storybook/test';
import { KitToolbar, KitToolbarWidget } from './kit-toolbar';

const meta: Meta<KitToolbarWidget> = {
  title: 'Kit/Toolbar Widget',
  component: KitToolbarWidget,
  decorators: [moduleMetadata({ imports: [KitToolbar, KitToolbarWidget] })],
  args: { invoked: fn() },
  render: (args) => ({ props: args, template: `<div fleetKitToolbar aria-label="Actions"><span fleetKitToolbarWidget (invoked)="invoked()">Accept</span><span fleetKitToolbarWidget [disabled]="true">Unavailable</span></div>` }),
};
export default meta;
type Story = StoryObj<KitToolbarWidget>;
export const Default: Story = {};
