import { signal } from '@angular/core';
import { provideRouter, withDisabledInitialNavigation } from '@angular/router';
import { applicationConfig } from '@storybook/angular-vite';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { TenantContext } from '../../core/auth/tenant-context';
import type { Meta, StoryObj } from '@storybook/angular-vite';
import type { UrgencyView } from 'fleet';
import { chunkSummary } from 'fleet/testing';
import { boardDonePolicy } from '../../core/board-done-policy.model';
import { glanceListGroups, glanceVitals } from './glance-board.model';
import { GlanceView } from './glance-view';

const confirmed = boardDonePolicy([{ chunks: [], next_cursor: null, board_done_policy: { window_seconds: 172800, floor: 10, done_since: '2026-10-30T00:00:00Z' } }]);
const generic = boardDonePolicy([]);
const list = glanceListGroups([chunkSummary({ chunk_id: 'ch_done', graph_id: 'gr_1', status: 'done', terminal: true, completed_at: '2026-10-01T00:00:00Z' })], [], [], [], [], new Map(), new Map(), new Map());
const empty = { live: [], ready: [], backlog: [], done: [] };
const meta: Meta<GlanceView> = {
  title: 'Hub board/Glance view', component: GlanceView,
  decorators: [applicationConfig({ providers: [provideRouter([{ path: 'board/chunk/:chunkId', children: [] }], withDisabledInitialNavigation())] })],
  args: { list, vitals: glanceVitals([], list), vitalsLoading: false, listState: 'ready', spendState: 'ready', spend: null, spendYesterday: null, donePolicy: confirmed, urgencies: [] },
  argTypes: {
    list: { control: 'object' },
    vitals: { control: 'object' },
    vitalsLoading: { control: 'boolean' },
    listState: { control: 'select', options: ['loading', 'error', 'empty', 'ready'] },
    spendState: { control: 'select', options: ['loading', 'error', 'empty', 'ready'] },
    spend: { control: 'object' },
    spendYesterday: { control: 'object' },
    donePolicy: { control: 'object' },
    urgencies: { control: 'object' },
  },
  globals: { viewport: { value: 'phone', isRotated: false } },
};
export default meta;
type Story = StoryObj<GlanceView>;
export const Confirmed: Story = {};
export const Generic: Story = { args: { donePolicy: generic } };
export const Empty: Story = { args: { list: empty, vitals: glanceVitals([], empty), listState: 'empty' } };
export const SpendLoading: Story = { args: { spendState: 'loading' } };
export const SpendError: Story = { args: { spendState: 'error' } };
export const SpendEmpty: Story = { args: { spendState: 'empty' } };

const blockedList = glanceListGroups([
  chunkSummary({ chunk_id: 'ch_blocked_one', graph_id: 'gr_1', status: 'ready', blocked: { prerequisite_chunk_id: 'ch_01M7KSQP45FAZ2X7ZVV0AMNGDA', unmet_count: 1 } }),
  chunkSummary({ chunk_id: 'ch_blocked_many', graph_id: 'gr_1', status: 'ready', blocked: { prerequisite_chunk_id: 'ch_01M7KSQP45FAZ2X7ZVV0AMNGDA', unmet_count: 3 } }),
  chunkSummary({ chunk_id: 'ch_unblocked', graph_id: 'gr_1', status: 'ready' }),
], [], [], [], [], new Map(), new Map(), new Map());
export const Blocked: Story = { args: { list: blockedList, vitals: glanceVitals([], blockedList) } };
const longBlockerList = { ...empty, ready: [{ ...blockedList.ready[0], blocked: { label: 'a-very-long-blocker-reference-that-exceeds-a-phone-width', title: 'a-very-long-blocker-reference-that-exceeds-a-phone-width' } }] };
export const LongBlocker: Story = { args: { list: longBlockerList, vitals: glanceVitals([], longBlockerList) } };

const urgencyProviders = applicationConfig({ providers: [provideTanStackQuery(new QueryClient()), { provide: TenantContext, useValue: { current: signal(null) } }] });
const urgent = { urgency_id: 'u', slug: 'urgent', name: 'Urgent', color: 'red', emoji: '🔥', icon: null, default: false, retired: false, place: 1 } as UrgencyView;
const urgentList = glanceListGroups([chunkSummary({ chunk_id: 'ch_urgent', graph_id: 'gr_1', status: 'ready', urgency: { urgency_id: 'u', slug: 'urgent' } })], [], [], [], [], new Map(), new Map(), new Map());
export const Urgency: Story = { decorators: [urgencyProviders], args: { list: urgentList, vitals: glanceVitals([], urgentList), urgencies: [urgent] } };
