import type { Meta, StoryObj } from '@storybook/angular-vite';
import { KitUrgencyPill } from './kit-urgency-pill';

const meta: Meta<KitUrgencyPill> = {
  title: 'Kit/Urgency Pill',
  component: KitUrgencyPill,
  args: { name: 'Standard', color: 'slate' },
};
export default meta;
type Story = StoryObj<KitUrgencyPill>;
export const Default: Story = {};
export const Emoji: Story = { args: { name: 'Urgent', color: 'red', emoji: '🔥', description: 'Needs attention soon' } };
export const CustomDark: Story = { args: { name: 'Custom', color: '#240066' } };
export const Image: Story = { args: { name: 'Image', color: 'cyan', iconUrl: 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20"%3E%3Ccircle cx="10" cy="10" r="8" fill="%2322d3ee"/%3E%3C/svg%3E' } };
