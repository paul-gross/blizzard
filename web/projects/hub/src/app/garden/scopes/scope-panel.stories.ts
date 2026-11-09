import type { Meta, StoryObj } from '@storybook/angular-vite';

import { FleetScopePanel, type ScopePanelVm } from './scope-panel';

const VM: ScopePanelVm = {
  slug: 'blizzard', description: 'The blizzard monorepo', retired: false, renderedRetired: false,
  relatedRoutines: [
    { slug: 'nightly', name: 'Nightly', isDefault: true },
    { slug: 'a-long-routine-name-for-wrapping', name: 'A long routine name for wrapping', isDefault: false },
  ],
  swept: { at: null, notSwept: 'not in 28 days' },
};
const meta: Meta<FleetScopePanel> = {
  title: 'Hub garden/Scope panel', component: FleetScopePanel,
  args: { vm: VM, state: 'ready', relatedRoutinesState: 'ready', hasProjectLens: true, project: 'Blizzard' },
};
export default meta;
type Story = StoryObj<FleetScopePanel>;
export const RelatedRoutinesLoaded: Story = {};
export const RelatedRoutinesPending: Story = { args: { vm: { ...VM, relatedRoutines: null }, relatedRoutinesState: 'loading' } };
export const RelatedRoutinesError: Story = { args: { vm: { ...VM, relatedRoutines: null }, relatedRoutinesState: 'error' } };
export const RelatedRoutinesEmpty: Story = { args: { vm: { ...VM, relatedRoutines: [] }, relatedRoutinesState: 'empty' } };
