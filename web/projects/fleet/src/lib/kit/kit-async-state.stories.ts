import type { Meta, StoryObj } from '@storybook/angular-vite';

import { KitAsyncState } from './kit-async-state';

const meta: Meta<KitAsyncState> = {
  title: 'Kit/Async state',
  component: KitAsyncState,
  args: {
    state: 'loading',
    loadingText: 'LOADING…',
    errorText: 'UNAVAILABLE',
    emptyText: 'NOTHING HERE',
    tone: 'default',
    placement: 'center',
    loadingMode: 'text',
    loadingTestid: null,
    errorTestid: null,
    emptyTestid: null,
  },
  render: (args) => ({
    props: args,
    template: `<div style="position: relative; height: 160px; border: 1px solid var(--line)"><fleet-kit-async-state [state]="state" [loadingText]="loadingText" [errorText]="errorText" [emptyText]="emptyText" [tone]="tone" [placement]="placement" [loadingMode]="loadingMode" [loadingTestid]="loadingTestid" [errorTestid]="errorTestid" [emptyTestid]="emptyTestid"><p style="margin: 0; padding: 8px">Four chunks in flight.</p><p loading style="margin: 0; padding: 8px">Skeleton placeholder</p></fleet-kit-async-state></div>`,
  }),
};
export default meta;

type Story = StoryObj<KitAsyncState>;

export const Loading: Story = {};
export const LoadingContent: Story = { args: { loadingMode: 'content' } };
export const Errored: Story = { args: { state: 'error' } };
export const Empty: Story = { args: { state: 'empty' } };
export const EmptyAccent: Story = {
  args: { state: 'empty', tone: 'accent', emptyText: 'NOT STARTED YET' },
};
export const Ready: Story = { args: { state: 'ready' } };
export const Inline: Story = { args: { state: 'empty', placement: 'inline' } };
export const InlineError: Story = {
  args: { state: 'error', placement: 'inline' },
};
export const LongText: Story = {
  args: {
    state: 'empty',
    emptyText:
      'NO CHUNKS MATCH THE CURRENT FILTERS — CLEAR A FILTER OR WIDEN THE DATE RANGE TO SEE MORE OF THE FLEET',
  },
};
export const LongTextPhone: Story = {
  args: LongText.args,
  globals: { viewport: { value: 'phone', isRotated: false } },
};
export const LongTextPhoneSmall: Story = {
  args: LongText.args,
  globals: { viewport: { value: 'phone-sm', isRotated: false } },
};
