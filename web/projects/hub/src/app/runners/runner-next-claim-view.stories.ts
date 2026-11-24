import { applicationConfig, type Meta, type StoryObj } from '@storybook/angular-vite';
import { provideRouter } from '@angular/router';
import type { Urgency } from 'fleet';
import { RunnerNextClaimView } from './runner-next-claim-view';
import type { NextClaim, NextClaimCandidate, NextClaimPassed, NextClaimScenario } from './runner-next-claim.domain';

const entry: NextClaimCandidate = { chunk_id: 'ch_01J0000000000000000000NEXT', graph_id: 'gr_1', position: 3, project_id: 'p', project_name: 'Blizzard', project_slug: 'blizzard', urgency: { id: 'u', slug: 'urgent' } };
const blocked: NextClaimPassed = { ...entry, chunk_id: 'ch_01J0000000000000000000PASS', reason: 'blocked', prerequisite: 'ch_01J0000000000000000000PREQ', prerequisite_count: 3 };
const urgency = { id: 'u', slug: 'urgent', name: 'Urgent', color: 'red', emoji: '🔥', icon: null, retired: false, default: false, place: 1, revision: 1, openChunks: 0, createdAt: '2026-01-01T00:00:00Z', createdBy: 'u' } satisfies Urgency;
const data: NextClaim = { next: entry, passed: [blocked], passed_total: 24, if_holding: { ...entry, chunk_id: 'ch_01J0000000000000000000HELD' }, held_at: [blocked], unserved: 2, without_repository_targets: null };
const alternative: NextClaimScenario = { next: { ...entry, chunk_id: 'ch_01J0000000000000000000ALTN', position: 7 }, passed: [], passed_total: 0, unserved: 0, if_holding: null, held_at: [], excluded: 2 };
const olderHub = (body: NextClaim) => Object.fromEntries(Object.entries(body).filter(([key]) => key !== 'without_repository_targets'));
const meta: Meta<RunnerNextClaimView> = {
  title: 'Hub fleet/Runner next claim', component: RunnerNextClaimView,
  decorators: [applicationConfig({ providers: [provideRouter([])] })],
  args: { runnerId: 'rn_1', state: 'ready', data, urgencies: [urgency], statuses: { [blocked.prerequisite!]: 'running' } },
};
export default meta;
type Story = StoryObj<RunnerNextClaimView>;
export const Next: Story = {};
export const WithAlternative: Story = { args: { data: { ...data, without_repository_targets: alternative } } };
export const OlderHub: Story = { args: { data: olderHub(data) as NextClaim } };
export const NothingClaimable: Story = { args: { data: { ...data, next: null, without_repository_targets: { ...alternative, next: null } } } };
export const Unavailable: Story = { args: { state: 'error', data: null } };
export const PhoneSmall: Story = { globals: { viewport: { value: 'phone-sm', isRotated: false } } };
