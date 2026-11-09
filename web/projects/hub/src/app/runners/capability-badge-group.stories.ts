import type { Meta, StoryObj } from '@storybook/angular-vite';
import { CapabilityBadgeGroup } from './capability-badge-group';

const meta: Meta<CapabilityBadgeGroup> = {
  title: 'Hub fleet/Capability badges',
  component: CapabilityBadgeGroup,
  args: { capabilities: [
    { harness_id: 'claude_code', version: '1', tiers: [], default: true, available: true },
    { harness_id: 'opencode', version: '1', tiers: [], default: false, available: false },
  ] },
};
export default meta;
type Story = StoryObj<CapabilityBadgeGroup>;
export const Ready: Story = {};
export const Empty: Story = { args: { capabilities: [] } };
export const PhoneSmall: Story = { globals: { viewport: { value: 'phone-sm', isRotated: false } } };
