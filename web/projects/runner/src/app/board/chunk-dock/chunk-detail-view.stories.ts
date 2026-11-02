import type { Meta, StoryObj } from '@storybook/angular-vite';
import type { runnerApi } from 'fleet';
import { fn } from 'storybook/test';

import { MachineDetailView } from './chunk-detail-view';

const LEASE: runnerApi.LeaseView = {
  lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNNEW1',
  chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
  graph_id: 'gr_1',
  node_id: 'nd_build',
  node_name: 'build',
  epoch: 2,
  session_id: 'sess-new',
  pid: null,
  environment_id: 'beta',
  workdir: '/ws/beta',
  created_at: '2026-07-16T11:00:00.000Z',
  last_heartbeat_at: '2026-07-16T11:59:26.000Z',
  state: 'closed',
  display_status: 'escalated',
  closure_healthy: false,
  closed_at: '2026-07-16T12:00:00.000Z',
  closure_reason: 'escalated',
  stale_after_seconds: 3600,
  resolved_model: null,
  resolved_effort: null,
  harness_version: null,
  git_commits: [],
  harness_id: 'claude_code',
  project_id: null,
};

const ESCALATION: runnerApi.EscalationView = {
  chunk_id: LEASE.chunk_id,
  lease_id: LEASE.lease_id,
  node_id: LEASE.node_id,
  epoch: LEASE.epoch,
  closed_at: '2026-07-16T12:00:00.000Z',
  resume_command: 'cd /ws/beta && claude --resume sess-new',
  wrapped_takeover_command: 'blizzard runner takeover ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
  closure: null,
  cause: null,
  effort: null,
  harness_id: 'claude_code',
  harness_version: '1.2.3',
  model: null,
  project_id: null,
  session_name: null,
};

const meta: Meta<MachineDetailView> = {
  title: 'Runner/Machine detail',
  component: MachineDetailView,
  args: {
    lease: LEASE, escalation: ESCALATION, heartbeatLabel: '—',
    requeuePending: false, requeueError: null, requeued: false, requeue: fn(),
  },
};
export default meta;

type Story = StoryObj<MachineDetailView>;

export const Escalated: Story = {};
export const RequeuePending: Story = { args: { requeuePending: true } };
export const Requeued: Story = { args: { requeued: true } };
export const RequeueRefused: Story = { args: { requeueError: 'Chunk has an open takeover — end the interactive session first.' } };
export const NoEscalation: Story = { args: { escalation: null } };
export const Phone: Story = { args: RequeueRefused.args, globals: { viewport: { value: 'phone', isRotated: false } } };
