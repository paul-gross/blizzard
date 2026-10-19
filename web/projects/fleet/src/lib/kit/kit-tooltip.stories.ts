import {
  moduleMetadata,
  type Meta,
  type StoryObj,
} from '@storybook/angular-vite';

import { KitButton } from './kit-button';
import { KitTooltip } from './kit-tooltip';

const meta: Meta<KitTooltip> = {
  title: 'Kit/Tooltip',
  component: KitTooltip,
  decorators: [moduleMetadata({ imports: [KitButton] })],
  args: { fleetTooltip: 'Retries from the last successful node-step' },
  render: (args) => ({
    props: args,
    template: `<div style="padding: 48px"><fleet-kit-button [fleetTooltip]="fleetTooltip">Retry chunk</fleet-kit-button></div>`,
  }),
};
export default meta;

type Story = StoryObj<KitTooltip>;

/** Closed: hover or focus the trigger to open it. */
export const OnButton: Story = {};
export const OnInlineText: Story = {
  render: (args) => ({
    props: args,
    template: `<p style="padding: 48px">The lease expires <span [fleetTooltip]="fleetTooltip" tabindex="0">in 12 minutes</span> unless renewed.</p>`,
  }),
};
export const LongText: Story = {
  args: {
    fleetTooltip:
      'Retries from the last successful node-step on a fresh lease, in a clean environment, discarding every uncommitted change the previous attempt left behind in its worktrees',
  },
};
