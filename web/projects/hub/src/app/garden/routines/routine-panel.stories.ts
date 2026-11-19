import type { Urgency } from 'fleet';
import { moduleMetadata, type Meta, type StoryObj } from '@storybook/angular-vite';

import { FleetRoutinePanel, type RoutinePanelVm } from './routine-panel';
import { RoutinePanelEditingStoryHost } from './routine-panel.story-host';

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

export const DefaultUrgency: Story = { args: { canEdit: true, editable: true, urgenciesReady: true, defaultUrgency: null,
  urgencies: [{ id: 'u', slug: 'normal', name: 'Normal', default: true, retired: false } as Urgency] } };

const URGENCIES = [
  { id: 'u1', slug: 'normal', name: 'Normal', default: true, retired: false } as Urgency,
  { id: 'u2', slug: 'urgent', name: 'Urgent', default: false, retired: false } as Urgency,
];

const editing = (attrs: string, args: Record<string, unknown> = {}): Story => ({
  decorators: [moduleMetadata({ imports: [RoutinePanelEditingStoryHost] })],
  args,
  render: (a) => ({
    props: { ...a, vm: VM, urgencies: URGENCIES },
    template: `<app-story-routine-panel-editing [vm]="vm" [urgencies]="urgencies" ${attrs} />`,
  }),
});

export const EditingGraph: Story = editing('');
export const EditingGraphPhone: Story = { ...EditingGraph, globals: { viewport: { value: 'phone', isRotated: false } } };
export const EditingGraphFieldError: Story = editing('[draft]="draft" fieldError="no graph is named that"', { draft: { graph: 'no-such-graph' } });
export const EditingGraphBlank: Story = editing('[draft]="draft" fieldError="a graph name is required"', { draft: { graph: '' } });
export const EditingUrgency: Story = editing('field="urgency" [draft]="draft"', { draft: { urgency: 'urgent' } });
export const EditingConflict: Story = editing(
  '[draft]="draft" formError="the routine changed since it was read — reload it and try again"',
  { draft: { graph: 'garden-delta' } },
);
export const EditingConflictPhone: Story = { ...EditingConflict, globals: { viewport: { value: 'phone', isRotated: false } } };
