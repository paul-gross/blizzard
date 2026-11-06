import type { Meta, StoryObj } from '@storybook/angular-vite';
import type { ChoreView } from 'fleet';
import { gardenCadenceAttentionView, gardenCadenceView, gardenChoreView } from 'fleet/testing';
import { fn } from 'storybook/test';

import { FleetCadenceList } from './cadence-list';
import { cadenceRows } from './gardening-cadences-page.model';

const ROWS = cadenceRows([
  gardenCadenceView({
    slug: 'evening',
    name: 'Evening sweep',
    chores: [gardenChoreView(), gardenChoreView({ chore_id: 'chr_2', key: 'docs' })],
    errand_counts: { due: 1, running: 1, waiting: 4, stopped: 0 },
  }),
  gardenCadenceView({
    slug: 'weekly-deep-review-of-every-scope-the-project-keeps',
    name: 'A weekly deep review of every scope the project keeps, named at length',
    schedule: { cron: '30 4 * * 1', zone: 'America/Argentina/ComodRivadavia' },
    spacing_minutes: 90,
    anchor: 'finished',
    chores: [gardenChoreView({ mode: 'sampled' as ChoreView['mode'] })],
    attention: [gardenCadenceAttentionView(), gardenCadenceAttentionView({ scope: 'other' })],
    errand_counts: { due: 0, running: 0, waiting: 1, stopped: 2 },
  }),
  gardenCadenceView({ slug: 'paused', retired: true, next_moments: [], schedule: null, chores: [gardenChoreView()] }),
]);

const meta: Meta<FleetCadenceList> = {
  title: 'Hub garden/Cadence list',
  component: FleetCadenceList,
  args: { rows: ROWS, state: 'ready', selectedSlug: 'evening', cadencePick: fn() },
};
export default meta;

type Story = StoryObj<FleetCadenceList>;

export const Listed: Story = {};
export const Phone: Story = { globals: { viewport: { value: 'phone', isRotated: false } } };
export const PhoneSmall: Story = { globals: { viewport: { value: 'phone-sm', isRotated: false } } };
export const Loading: Story = { args: { rows: [], state: 'loading' } };
export const Empty: Story = { args: { rows: [], state: 'empty' } };
export const Failed: Story = { args: { rows: [], state: 'error' } };
