import type { Meta, StoryObj } from '@storybook/angular-vite';

import { GateBadgeGroup } from './gate-badge-group';

const meta: Meta<GateBadgeGroup> = {
  title: 'Host/Gate badges',
  component: GateBadgeGroup,
  args: { gates: ['build', 'deliver', 'unknown-node'], unenforceable: [
    { node: 'deliver', reason: 'hub-executed' },
    { node: 'unknown-node', reason: 'unknown-node' },
  ] },
};
export default meta;
type Story = StoryObj<GateBadgeGroup>;
export const Mixed: Story = {};
export const Unlabelled: Story = { args: { labelled: false } };
export const None: Story = { args: { gates: [] } };
export const LongGate: Story = { args: {
  gates: ['unbrokengatename'.repeat(12)],
  unenforceable: [],
} };
