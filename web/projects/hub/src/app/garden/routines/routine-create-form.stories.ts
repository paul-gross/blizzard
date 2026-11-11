import type { Meta, StoryObj } from '@storybook/angular-vite';
import type { UrgencyView } from 'fleet';
import { RoutineCreateForm } from './routine-create-form';

const meta: Meta<RoutineCreateForm> = {
  title: 'Hub garden/Routine create form', component: RoutineCreateForm,
  args: { urgencies: [
    { urgency_id: 'u1', slug: 'normal', name: 'Normal', default: true, retired: false } as UrgencyView,
    { urgency_id: 'u2', slug: 'urgent', name: 'Urgent', default: false, retired: false } as UrgencyView,
  ] },
};
export default meta;
type Story = StoryObj<RoutineCreateForm>;
export const Default: Story = {};
export const Phone: Story = { globals: { viewport: { value: 'phone-sm', isRotated: false } } };
export const Refused: Story = { args: { error: 'The scope was not found.' } };
