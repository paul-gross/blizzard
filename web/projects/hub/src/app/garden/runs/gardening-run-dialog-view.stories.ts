import type { Meta, StoryObj } from '@storybook/angular-vite';
import type { ScopeView, UrgencyView } from 'fleet';
import { GardeningRunDialogView } from './gardening-run-dialog-view';

const meta: Meta<GardeningRunDialogView> = {
  title: 'Hub garden/Run routine', component: GardeningRunDialogView,
  args: { routineName: 'Nightly', state: 'ready', scopes: [{ slug: 'web', description: 'Web' } as ScopeView], sweptSlugs: new Set<string>(), baselines: [], routineUrgency: 'urgent', urgencies: [
    { urgency_id: 'n', slug: 'normal', name: 'Normal', default: true, retired: false } as UrgencyView,
    { urgency_id: 'u', slug: 'urgent', name: 'Urgent', default: false, retired: false } as UrgencyView,
  ] },
};
export default meta;
type Story = StoryObj<GardeningRunDialogView>;
export const Default: Story = {};
export const Loading: Story = { args: { state: 'loading' } };
export const Error: Story = { args: { state: 'error' } };
export const Empty: Story = { args: { state: 'empty', scopes: [] } };
export const Phone: Story = { globals: { viewport: { value: 'phone-sm', isRotated: false } } };
export const SubmitError: Story = { args: { submitError: 'The run could not be submitted. Please try again.' } };
export const Submitting: Story = { args: { submitting: true } };
