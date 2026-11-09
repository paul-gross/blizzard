import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { FleetCadenceForm } from './cadence-form';
import { EMPTY_CADENCE_DRAFT } from './gardening-cadences-page.model';

const meta: Meta<FleetCadenceForm> = {
  title: 'Hub garden/Cadence form',
  component: FleetCadenceForm,
  args: { initial: EMPTY_CADENCE_DRAFT, creating: true, pending: false, error: null, saved: fn(), cancelled: fn() },
};
export default meta;

type Story = StoryObj<FleetCadenceForm>;

export const Create: Story = {};
export const CreatePhone: Story = { globals: { viewport: { value: 'phone-sm', isRotated: false } } };
export const Edit: Story = {
  args: {
    creating: false,
    initial: { slug: 'evening', name: 'Evening sweep', cron: '0 20 * * *', zone: 'America/Chicago', spacing: '10d', anchor: 'finished' },
  },
};
export const Refused: Story = { args: { error: 'schedule: a cron expression has five fields, got "0 20 * *"' } };
export const Pending: Story = { args: { pending: true } };
