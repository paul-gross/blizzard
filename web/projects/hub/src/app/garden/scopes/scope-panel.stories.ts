import { moduleMetadata, type Meta, type StoryObj } from '@storybook/angular-vite';

import type { ScopePanelVm } from './scope-panel';
import { ScopePanelStoryHost } from './scope-panel.story-host';

const VM: ScopePanelVm = {
  slug: 'blizzard', description: 'The blizzard monorepo', retired: false, renderedRetired: false,
  relatedRoutines: [
    { slug: 'nightly', name: 'Nightly', isDefault: true },
    { slug: 'a-long-routine-name-for-wrapping', name: 'A long routine name for wrapping', isDefault: false },
  ],
  swept: { at: null, notSwept: 'not in 28 days' },
};
const meta: Meta<ScopePanelStoryHost> = {
  title: 'Hub garden/Scope panel', component: ScopePanelStoryHost,
  decorators: [moduleMetadata({ imports: [ScopePanelStoryHost] })],
  args: { vm: VM, state: 'ready', relatedRoutinesState: 'ready', project: 'Blizzard' },
};
export default meta;
type Story = StoryObj<ScopePanelStoryHost>;
export const RelatedRoutinesLoaded: Story = {};
export const RelatedRoutinesPending: Story = { args: { vm: { ...VM, relatedRoutines: null }, relatedRoutinesState: 'loading' } };
export const RelatedRoutinesError: Story = { args: { vm: { ...VM, relatedRoutines: null }, relatedRoutinesState: 'error' } };
export const RelatedRoutinesEmpty: Story = { args: { vm: { ...VM, relatedRoutines: [] }, relatedRoutinesState: 'empty' } };
export const EditingDescription: Story = { args: { editing: true } };
export const DescriptionRefused: Story = { args: { editing: true, fieldError: 'Description is too long' } };
export const DescriptionFormRefused: Story = { args: { editing: true, formError: 'Set description failed.' } };
