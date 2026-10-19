import {
  moduleMetadata,
  type Meta,
  type StoryObj,
} from '@storybook/angular-vite';

import { KitBadge } from './kit-badge';
import { KitLinkTile } from './kit-link-tile';

const meta: Meta<KitLinkTile> = {
  title: 'Kit/Link tile',
  component: KitLinkTile,
  decorators: [moduleMetadata({ imports: [KitBadge] })],
  render: () => ({
    template: `
      <a fleetKitLinkTile href="#" style="width: 320px; padding: 10px 12px; gap: 6px">
        <span style="display: flex; justify-content: space-between; gap: 8px">
          <strong>Story the kit's controls</strong>
          <fleet-kit-badge tone="running">running</fleet-kit-badge>
        </span>
        <span>build · epoch 2 · r-claude</span>
        <span>3 repositories · updated 4 minutes ago</span>
      </a>`,
  }),
};
export default meta;

type Story = StoryObj<KitLinkTile>;

export const HeadAndFacts: Story = {};
export const MinimalBody: Story = {
  render: () => ({
    template: `<a fleetKitLinkTile href="#" style="width: 320px; padding: 10px 12px">Story the kit's controls</a>`,
  }),
};
export const LongContent: Story = {
  render: () => ({
    template: `
      <a fleetKitLinkTile href="#" style="width: 320px; padding: 10px 12px; gap: 6px">
        <span style="display: flex; justify-content: space-between; gap: 8px">
          <strong>Give each of the eighteen control parts its own stories file, following the spec's naming and wiring rules</strong>
          <fleet-kit-badge tone="running">running</fleet-kit-badge>
        </span>
        <span>ch_01M57RP7EGQTX461G740Z5WS6G_lease_01M58V8E0A3XPZMXMTPCKVB77F_reviewed_plan_artifact</span>
      </a>`,
  }),
};
