import { moduleMetadata, type Meta, type StoryObj } from '@storybook/angular-vite';
import type { Urgency } from 'fleet';

import { gardenRoutineBaseline } from '../core/run.builders';
import { gardenScope } from '../core/scope.builders';

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
    scopes: [gardenScope({ slug: 'web', description: 'Web' }), gardenScope({ slug: 'hub', description: 'Hub' })],
    urgencies: [
      { id: 'n', slug: 'normal', name: 'Normal', default: true, retired: false } as Urgency,
      { id: 'u', slug: 'urgent', name: 'Urgent', default: false, retired: false } as Urgency,
    ],
    draft: { scope: 'web' },
  },
});

const BASELINE = gardenRoutineBaseline({
  scopeSlug: 'web',
  findingSetId: 'fins_02STORY',
  recordedAt: '2026-02-01T00:00:00Z',
  repos: [{ repo: 'blizzard', revision: 'abc123d', landedSince: 3 }],
});

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
