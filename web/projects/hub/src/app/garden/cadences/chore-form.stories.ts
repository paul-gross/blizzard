import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { FleetChoreForm } from './chore-form';

const meta: Meta<FleetChoreForm> = {
  title: 'Hub garden/Chore form',
  component: FleetChoreForm,
  args: {
    initial: { key: '', routine: '', scopes: 'all', mode: 'full', promote: true, note: '' },
    keyEditable: true,
    pending: false,
    error: null,
    saved: fn(),
    cancelled: fn(),
  },
};
export default meta;

type Story = StoryObj<FleetChoreForm>;

export const Add: Story = {};
export const AddPhone: Story = { globals: { viewport: { value: 'phone-sm', isRotated: false } } };
export const Edit: Story = {
  args: { keyEditable: false, initial: { key: 'nightly', routine: 'nightly', scopes: 'web, hub', mode: 'delta', promote: false, note: 'docs only' } },
};
export const UnknownMode: Story = {
  args: { keyEditable: false, initial: { key: 'odd', routine: 'docs', scopes: 'all', mode: 'sampled', promote: true, note: '' } },
};
export const Refused: Story = { args: { error: 'promoting a chore needs chunk:control' } };
