import { signal } from '@angular/core';
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
  args: { list, vitals: glanceVitals([], list), listState: 'ready', spendState: 'ready', donePolicy: confirmed },
  globals: { viewport: { value: 'phone', isRotated: false } },
};
export default meta;
type Story = StoryObj<GlanceView>;
export const Confirmed: Story = {};
export const Generic: Story = { args: { donePolicy: generic } };
export const Empty: Story = { args: { list: empty, vitals: glanceVitals([], empty), listState: 'empty' } };

const urgencyProviders = applicationConfig({ providers: [provideTanStackQuery(new QueryClient()), { provide: TenantContext, useValue: { current: signal(null) } }] });
const urgent = { urgency_id: 'u', slug: 'urgent', name: 'Urgent', color: 'red', emoji: '🔥', icon: null, default: false, retired: false, place: 1 } as UrgencyView;
const urgentList = glanceListGroups([chunkSummary({ chunk_id: 'ch_urgent', graph_id: 'gr_1', status: 'ready', urgency: { urgency_id: 'u', slug: 'urgent' } })], [], [], [], [], new Map(), new Map(), new Map());
export const Urgency: Story = { decorators: [urgencyProviders], args: { list: urgentList, vitals: glanceVitals([], urgentList), urgencies: [urgent] } };
