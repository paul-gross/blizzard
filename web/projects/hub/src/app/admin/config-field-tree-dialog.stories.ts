import { moduleMetadata, type Meta, type StoryObj } from '@storybook/angular-vite';

import { SettingDialogHost, SourceDialogHost, TimeoutDialogHost, UrgencyDialogHost } from './config-field-tree-dialog.story-host';
import { ProjectedDialogHost } from './config-field-tree-dialog.projected.story-host';

const meta: Meta = {
  title: 'Hub admin/Config field-tree dialog',
  decorators: [moduleMetadata({ imports: [SettingDialogHost, SourceDialogHost, TimeoutDialogHost, UrgencyDialogHost, ProjectedDialogHost] })],
  parameters: { layout: 'fullscreen' },
};
export default meta;

type Story = StoryObj;

const create = (attrs = ''): Story => ({ render: () => ({ template: `<app-story-source-dialog ${attrs} />` }) });
const edit = (attrs = ''): Story => ({ render: () => ({ template: `<app-story-urgency-dialog ${attrs} />` }) });

export const Create: Story = create();
export const CreateTracker: Story = create('kind="tracker"');
export const Edit: Story = edit();
export const ProjectedEmpty: Story = { render: () => ({ template: '<app-story-projected-dialog />' }) };
export const ProjectedOneChange: Story = { render: () => ({ template: '<app-story-projected-dialog [initialChanges]="1" />' }) };
export const ProjectedManyChanges: Story = { render: () => ({ template: '<app-story-projected-dialog [initialChanges]="2" />' }) };
export const ProjectedPhone: Story = { ...ProjectedManyChanges, globals: { viewport: { value: 'phone', isRotated: false } } };
export const FieldRefusal: Story = {
  render: () => ({
    props: { outcome: { ok: false, fields: { name: 'That name is taken.' }, form: null } },
    template: '<app-story-source-dialog [outcome]="outcome" />',
  }),
};
export const RootRefusal: Story = {
  render: () => ({
    props: { outcome: { ok: false, fields: {}, form: 'The hub could not save this right now.' } },
    template: '<app-story-urgency-dialog [outcome]="outcome" />',
  }),
};
const timeout = (attrs = ''): Story => ({ render: () => ({ template: `<app-story-timeout-dialog ${attrs} />` }) });

export const DefaultChoice: Story = timeout();
export const StatedHours: Story = timeout('[useDefault]="false"');
export const DefaultChoicePhone: Story = { ...DefaultChoice, globals: { viewport: { value: 'phone', isRotated: false } } };
const setting = (attrs = ''): Story => ({ render: () => ({ template: `<app-story-setting-dialog ${attrs} />` }) });

export const TextDefaultChoice: Story = setting();
export const TextStated: Story = setting('[useDefault]="false"');
export const TextDefaultChoicePhone: Story = { ...TextDefaultChoice, globals: { viewport: { value: 'phone', isRotated: false } } };
export const CreatePhone: Story = { ...Create, globals: { viewport: { value: 'phone', isRotated: false } } };
export const EditPhoneSm: Story = { ...Edit, globals: { viewport: { value: 'phone-sm', isRotated: false } } };
