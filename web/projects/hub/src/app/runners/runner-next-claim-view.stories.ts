import { applicationConfig, type Meta, type StoryObj } from '@storybook/angular-vite';
import { provideRouter } from '@angular/router';
import type { hubApi, UrgencyView } from 'fleet';
import { RunnerNextClaimView } from './runner-next-claim-view';

const entry: hubApi.NextClaimEntry = { chunk_id: 'ch_01J0000000000000000000NEXT', graph_id: 'gr_1', position: 3, project_id: 'p', project_name: 'Blizzard', project_slug: 'blizzard', urgency: { urgency_id: 'u', slug: 'urgent' } };
const blocked: hubApi.NextClaimPassedEntry = { ...entry, chunk_id: 'ch_01J0000000000000000000PASS', reason: 'blocked', prerequisite: 'ch_01J0000000000000000000PREQ', prerequisite_count: 3 };
const urgency = { urgency_id: 'u', slug: 'urgent', name: 'Urgent', color: 'red', emoji: '🔥', default: false } as UrgencyView;
const data: hubApi.NextClaimResponse = { next: entry, passed: [blocked], passed_total: 24, if_holding: { ...entry, chunk_id: 'ch_01J0000000000000000000HELD' }, held_at: [blocked], unserved: 2 };
const meta: Meta<RunnerNextClaimView> = {
  title: 'Hub fleet/Runner next claim', component: RunnerNextClaimView,
  decorators: [applicationConfig({ providers: [provideRouter([])] })],
  args: { runnerId: 'rn_1', state: 'ready', data, urgencies: [urgency], statuses: { [blocked.prerequisite!]: 'running' } },
};
export default meta;
type Story = StoryObj<RunnerNextClaimView>;
export const Next: Story = {};
export const NothingClaimable: Story = { args: { data: { ...data, next: null } } };
export const Unavailable: Story = { args: { state: 'error', data: null } };
export const PhoneSmall: Story = { globals: { viewport: { value: 'phone-sm', isRotated: false } } };
