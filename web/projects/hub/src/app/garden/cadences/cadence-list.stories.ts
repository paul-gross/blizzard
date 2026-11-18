import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { gardenCadence, gardenCadenceAttention, gardenChore } from '../core/cadence.builders';

import { FleetCadenceList } from './cadence-list';
import { cadenceRows } from './gardening-cadences-page.model';

const ROWS = cadenceRows([
  gardenCadence({
    slug: 'evening',
    name: 'Evening sweep',
    chores: [gardenChore(), gardenChore({ id: 'chr_2', key: 'docs' })],
    errandCounts: { due: 1, running: 1, waiting: 4, stopped: 0 },
  }),
  gardenCadence({
    slug: 'weekly-deep-review-of-every-scope-the-project-keeps',
    name: 'A weekly deep review of every scope the project keeps, named at length',
    schedule: { cron: '30 4 * * 1', zone: 'America/Argentina/ComodRivadavia' },
    spacingMinutes: 90,
    anchor: 'finished',
    chores: [gardenChore({ mode: { unlisted: 'sampled' } })],
    attention: [gardenCadenceAttention(), gardenCadenceAttention({ scope: 'other' })],
    errandCounts: { due: 0, running: 0, waiting: 1, stopped: 2 },
  }),
  gardenCadence({ slug: 'paused', retired: true, nextMoments: [], schedule: null, chores: [gardenChore()] }),
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
