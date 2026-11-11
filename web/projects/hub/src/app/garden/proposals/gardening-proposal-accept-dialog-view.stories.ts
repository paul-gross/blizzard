import type { Meta, StoryObj } from '@storybook/angular-vite';
import type { UrgencyView } from 'fleet';
import { GardeningProposalAcceptDialogView } from './gardening-proposal-accept-dialog-view';

const meta: Meta<GardeningProposalAcceptDialogView> = {
  title: 'Hub garden/Accept proposal', component: GardeningProposalAcceptDialogView,
  args: { proposalTitle: 'Improve the garden', proposalBody: 'Describe the proposed work.', urgencies: [
    { urgency_id: 'n', slug: 'normal', name: 'Normal', default: true, retired: false } as UrgencyView,
    { urgency_id: 'u', slug: 'urgent', name: 'Urgent', default: false, retired: false } as UrgencyView,
  ] },
};
export default meta;
type Story = StoryObj<GardeningProposalAcceptDialogView>;
export const Default: Story = {};
export const Phone: Story = { globals: { viewport: { value: 'phone-sm', isRotated: false } } };
