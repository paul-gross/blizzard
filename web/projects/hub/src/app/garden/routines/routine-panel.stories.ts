import type { Meta, StoryObj } from '@storybook/angular-vite';

import { FleetRoutinePanel, type RoutinePanelVm } from './routine-panel';

const VM: RoutinePanelVm = {
  record: { slug: 'nightly', name: 'Nightly', graphName: 'garden', defaultScopeSlug: 'blizzard', modelEffort: 'sonnet · medium' },
  blockedReason: null,
  strategy: [], trend: null, measurements: [], lastSwept: [], windowLabel: 'last 28 days',
  relatedScopes: [{ slug: 'blizzard', isDefault: true }, { slug: 'a-long-related-scope-name-for-wrapping', isDefault: false }],
  retired: false, renderedRetired: false, recentRuns: [], recentRunsState: 'empty', measurementsState: 'empty',
  runsLabel: '0 in the last 28 days', keptBy: [], keptByState: 'empty',
};

const meta: Meta<FleetRoutinePanel> = {
  title: 'Hub garden/Routine panel', component: FleetRoutinePanel,
  args: { vm: VM, state: 'ready', relatedScopesState: 'ready', project: 'Blizzard' },
};
export default meta;
type Story = StoryObj<FleetRoutinePanel>;
export const RelatedScopesLoaded: Story = {};
export const RelatedScopesPending: Story = { args: { vm: { ...VM, relatedScopes: null }, relatedScopesState: 'loading' } };
export const RelatedScopesError: Story = { args: { vm: { ...VM, relatedScopes: null }, relatedScopesState: 'error' } };
export const RelatedScopesEmpty: Story = { args: { vm: { ...VM, relatedScopes: [] }, relatedScopesState: 'empty' } };
