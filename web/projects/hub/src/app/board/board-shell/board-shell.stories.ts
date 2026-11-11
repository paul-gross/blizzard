import { signal } from '@angular/core';
import { applicationConfig } from '@storybook/angular-vite';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { TenantContext } from '../../core/auth/tenant-context';
import type { Meta, StoryObj } from '@storybook/angular-vite';
import type { UrgencyView } from 'fleet';
import { chunkSummary } from 'fleet/testing';
import { boardDonePolicy } from '../../core/board-done-policy.model';
import { BoardShell } from './board-shell';

const confirmed = boardDonePolicy([{ chunks: [], next_cursor: null, board_done_policy: { window_seconds: 172800, floor: 10, done_since: '2026-10-30T00:00:00Z' } }]);
const multiplePages = boardDonePolicy([
  { chunks: [], next_cursor: 'next', board_done_policy: { window_seconds: 172800, floor: 10, done_since: '2026-10-30T00:00:00Z' } },
  { chunks: [], next_cursor: null, board_done_policy: { window_seconds: 172800, floor: 10, done_since: '2026-10-30T00:00:01Z' } },
]);
const generic = boardDonePolicy([]);
const chunks = [chunkSummary({ chunk_id: 'ch_done', graph_id: 'gr_1', status: 'done', terminal: true, completed_at: '2026-10-01T00:00:00Z' })];

const meta: Meta<BoardShell> = {
  title: 'Hub board/Board shell', component: BoardShell,
  args: { chunks, state: 'ready', donePolicy: confirmed },
};
export default meta;
type Story = StoryObj<BoardShell>;
export const Confirmed: Story = {};
export const MultiplePages: Story = { args: { donePolicy: multiplePages } };
export const Generic: Story = { args: { donePolicy: generic } };
export const Empty: Story = { args: { chunks: [], state: 'empty' } };

const urgencyProviders = applicationConfig({ providers: [provideTanStackQuery(new QueryClient()), { provide: TenantContext, useValue: { current: signal(null) } }] });
const urgent = { urgency_id: 'u', slug: 'urgent', name: 'Urgent', color: 'red', emoji: '🔥', icon: null, default: false, retired: false, place: 1 } as UrgencyView;
const urgencyChunks = [chunkSummary({ chunk_id: 'ch_urgent', graph_id: 'gr_1', status: 'ready', urgency: { urgency_id: 'u', slug: 'urgent' } }), ...chunks];
export const UrgencyCards: Story = { decorators: [urgencyProviders], args: { chunks: urgencyChunks, urgencies: [urgent] } };
export const UrgencyList: Story = { decorators: [urgencyProviders], args: { chunks: urgencyChunks, urgencies: [urgent], layout: 'list' } };
