import { moduleMetadata, type Meta, type StoryObj } from '@storybook/angular-vite';
import { gardenCadenceErrandView, gardenErrandRunView } from 'fleet/testing';
import { fn } from 'storybook/test';

import { gardenCadence, gardenCadenceAttention, gardenChore } from '../core/cadence.builders';

import { FleetCadencePanel } from './cadence-panel';
import { CadencePanelChoreStoryHost, CadencePanelEditingStoryHost } from './cadence-panel.story-host';
import { cadencePanelVm } from './gardening-cadence-detail.model';

const CADENCE = gardenCadence({
  slug: 'evening',
  name: 'Evening sweep',
  chores: [
    gardenChore({ note: 'the nightly docs pass, over every linked scope' }),
    gardenChore({
      id: 'chr_2',
      key: 'samples',
      routine: 'a-routine-with-a-rather-long-slug-for-wrapping',
      scopes: ['web', 'hub', 'gone'],
      mode: { unlisted: 'sampled' },
      promote: false,
    }),
  ],
  attention: [gardenCadenceAttention({ chore: 'samples' })],
});

const ERRANDS = [
  gardenCadenceErrandView({ scope: 'blizzard', last_run: gardenErrandRunView() }),
  gardenCadenceErrandView({ scope: 'blizzard-mock', state: 'running', due_at: null, last_run: gardenErrandRunView({ status: 'running', finished_at: null }) }),
  gardenCadenceErrandView({ chore: 'samples', scope: 'web', state: 'due' }),
  gardenCadenceErrandView({ chore: 'samples', scope: 'gone', state: 'stopped', due_at: null, attention: [{ chore: 'samples', scope: 'gone', reason: 'scope_unknown' }] }),
];

const VM = cadencePanelVm(CADENCE, null, ERRANDS);

const meta: Meta<FleetCadencePanel> = {
  title: 'Hub garden/Cadence panel',
  component: FleetCadencePanel,
  args: {
    vm: VM,
    state: 'ready',
    canEdit: true,
    actionError: null,
    editPending: false,
    lifecyclePending: false,
    editForm: null,
    startEdit: fn(),
    saveEdit: fn(),
    cancelEdit: fn(),
    choreForm: null,
    removing: null,
    startAddChore: fn(),
    startEditChore: fn(),
    saveChore: fn(),
    cancelChore: fn(),
    startRemoveChore: fn(),
    confirmRemoveChore: fn(),
    cancelRemoveChore: fn(),
    retire: fn(),
    enable: fn(),
  },
};
export default meta;

type Story = StoryObj<FleetCadencePanel>;

export const Kept: Story = {};
export const Phone: Story = { globals: { viewport: { value: 'phone', isRotated: false } } };
export const PhoneSmall: Story = { globals: { viewport: { value: 'phone-sm', isRotated: false } } };
export const ReadOnly: Story = { args: { canEdit: false } };
export const ErrandsLoading: Story = { args: { vm: cadencePanelVm(CADENCE, null, undefined) } };
export const ErrandsError: Story = { args: { vm: cadencePanelVm(CADENCE, null, undefined, { isPending: () => false, isError: () => true }) } };
export const ErrandsEmpty: Story = { args: { vm: cadencePanelVm(CADENCE, null, []) } };
export const Retired: Story = {
  args: { vm: cadencePanelVm({ ...CADENCE, retired: true, nextMoments: [], attention: [] }, null, ERRANDS.map((e) => ({ ...e, state: 'stopped' as const, due_at: null }))) },
};
export const NoChores: Story = { args: { vm: cadencePanelVm({ ...CADENCE, chores: [], attention: [gardenCadenceAttention({ chore: null, scope: null, reason: 'no_chores' })] }, null, []) } };
export const Refused: Story = { args: { actionError: 'the cadence changed since it was read — reload it and try again' } };
export const NothingSelected: Story = { args: { vm: null, state: 'empty' } };
export const Editing: Story = {
  decorators: [moduleMetadata({ imports: [CadencePanelEditingStoryHost] })],
  render: () => ({ props: { vm: VM }, template: '<app-story-cadence-panel-editing [vm]="vm" />' }),
};
export const EditingPhone: Story = { ...Editing, globals: { viewport: { value: 'phone', isRotated: false } } };
export const ChoreAdding: Story = {
  decorators: [moduleMetadata({ imports: [CadencePanelChoreStoryHost] })],
  render: () => ({ props: { vm: VM }, template: '<app-story-cadence-panel-chore [vm]="vm" [adding]="true" />' }),
};
export const ChoreEditing: Story = {
  decorators: [moduleMetadata({ imports: [CadencePanelChoreStoryHost] })],
  render: () => ({ props: { vm: VM }, template: '<app-story-cadence-panel-chore [vm]="vm" [adding]="false" />' }),
};
export const ChoreEditingPhone: Story = { ...ChoreEditing, globals: { viewport: { value: 'phone', isRotated: false } } };
export const RemovingChore: Story = { args: { removing: 'samples' } };
