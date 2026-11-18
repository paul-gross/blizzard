import { moduleMetadata, type Meta, type StoryObj } from '@storybook/angular-vite';
import type { RoutineBaselineView, ScopeView, Urgency } from 'fleet';

import { RunDialogStoryHost } from './gardening-run-dialog-view.story-host';

const meta: Meta = {
  title: 'Hub garden/Run routine',
  decorators: [moduleMetadata({ imports: [RunDialogStoryHost] })],
};
export default meta;

type Story = StoryObj;

const host = (attrs = ''): Story => ({
  render: (args) => ({
    props: args,
    template: `<app-story-run-dialog [scopes]="scopes" [urgencies]="urgencies" [draft]="draft" ${attrs} />`,
  }),
  args: {
    scopes: [
      { slug: 'web', description: 'Web' } as ScopeView,
      { slug: 'hub', description: 'Hub' } as ScopeView,
    ],
    urgencies: [
      { id: 'n', slug: 'normal', name: 'Normal', default: true, retired: false } as Urgency,
      { id: 'u', slug: 'urgent', name: 'Urgent', default: false, retired: false } as Urgency,
    ],
    draft: { scope: 'web' },
  },
});

const BASELINE = {
  scope_slug: 'web',
  finding_set_id: 'fins_02STORY',
  recorded_at: '2026-02-01T00:00:00Z',
  repos: [{ repo: 'blizzard', revision: 'abc123d', landed_since: 3 }],
} as RoutineBaselineView;

export const Default: Story = host();
export const Loading: Story = host('state="loading"');
export const Error: Story = host('state="error"');
export const Empty: Story = host('state="empty"');
export const Phone: Story = { ...Default, globals: { viewport: { value: 'phone-sm', isRotated: false } } };
export const DeltaWithBaseline: Story = {
  ...host('[swept]="swept" [baseline]="baseline"'),
  args: { ...Default.args, swept: new Set(['web']), baseline: BASELINE, draft: { scope: 'web', mode: 'delta' } },
};
export const SubmitError: Story = host('formError="The run could not be submitted. Please try again."');
export const ScopeRefused: Story = host(`scopeError="scope 'web' is retired"`);
export const Submitting: Story = host('[submitting]="true"');
