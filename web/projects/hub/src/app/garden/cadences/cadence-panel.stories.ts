import type { Meta, StoryObj } from '@storybook/angular-vite';
import type { ChoreView } from 'fleet';
import {
  gardenCadenceAttentionView,
  gardenCadenceErrandView,
  gardenCadenceView,
  gardenChoreView,
  gardenErrandRunView,
} from 'fleet/testing';
import { fn } from 'storybook/test';

import { FleetCadencePanel } from './cadence-panel';
import { cadencePanelVm } from './gardening-cadence-detail.model';

const CADENCE = gardenCadenceView({
  slug: 'evening',
  name: 'Evening sweep',
  chores: [
    gardenChoreView({ note: 'the nightly docs pass, over every linked scope' }),
    gardenChoreView({
      chore_id: 'chr_2',
      key: 'samples',
      routine: 'a-routine-with-a-rather-long-slug-for-wrapping',
      scopes: ['web', 'hub', 'gone'],
      mode: 'sampled' as ChoreView['mode'],
      promote: false,
    }),
  ],
  attention: [gardenCadenceAttentionView({ chore: 'samples' })],
});

const ERRANDS = [
  gardenCadenceErrandView({ scope: 'blizzard', last_run: gardenErrandRunView() }),
  gardenCadenceErrandView({ scope: 'blizzard-mock', state: 'running', due_at: null, last_run: gardenErrandRunView({ status: 'running', finished_at: null }) }),
  gardenCadenceErrandView({ chore: 'samples', scope: 'web', state: 'due' }),
  gardenCadenceErrandView({ chore: 'samples', scope: 'gone', state: 'stopped', due_at: null, attention: [gardenCadenceAttentionView({ chore: 'samples' })] }),
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
    edit: fn(),
    addChore: fn(),
    editChore: fn(),
    removeChore: fn(),
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
export const Retired: Story = {
  args: { vm: cadencePanelVm({ ...CADENCE, retired: true, next_moments: [], attention: [] }, null, ERRANDS.map((e) => ({ ...e, state: 'stopped' as const, due_at: null }))) },
};
export const NoChores: Story = { args: { vm: cadencePanelVm({ ...CADENCE, chores: [], attention: [gardenCadenceAttentionView({ chore: null, scope: null, reason: 'no_chores' })] }, null, []) } };
export const Refused: Story = { args: { actionError: 'the cadence changed since it was read — reload it and try again' } };
export const NothingSelected: Story = { args: { vm: null, state: 'empty' } };
