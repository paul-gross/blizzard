import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { KitDialog } from './kit-dialog';

const meta: Meta<KitDialog> = {
  title: 'Kit/Dialog',
  component: KitDialog,
  args: {
    open: true,
    ariaLabel: 'Discard draft',
    testid: null,
    closed: fn(),
  },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-dialog [open]="open" [ariaLabel]="ariaLabel" [testid]="testid" (closed)="closed()">
  <span fleetKitDialogHeader>Discard draft?</span>
  <p>The unsaved changes to this chunk will be lost.</p>
  <span fleetKitDialogFooter><button type="button">Keep editing</button> <button type="button">Discard</button></span>
</fleet-kit-dialog>`,
  }),
};
export default meta;

type Story = StoryObj<KitDialog>;

export const Default: Story = {};
export const Closed: Story = { args: { open: false } };
export const LongContent: Story = {
  render: (args) => ({
    props: args,
    template: `<fleet-kit-dialog [open]="open" [ariaLabel]="ariaLabel" [testid]="testid" (closed)="closed()">
  <span fleetKitDialogHeader>Discard the draft of the chunk awaiting a human answer on a question the plan could not decide?</span>
  <p>The unsaved changes to this chunk will be lost. Its plan, its node history, its escalations and every answer already recorded against it stay as they are; only the text typed into this editor since the last save goes away.</p>
  <p>The unsaved changes to this chunk will be lost. Its plan, its node history, its escalations and every answer already recorded against it stay as they are; only the text typed into this editor since the last save goes away.</p>
  <p>The unsaved changes to this chunk will be lost. Its plan, its node history, its escalations and every answer already recorded against it stay as they are; only the text typed into this editor since the last save goes away.</p>
  <p>The unsaved changes to this chunk will be lost. Its plan, its node history, its escalations and every answer already recorded against it stay as they are; only the text typed into this editor since the last save goes away.</p>
  <span fleetKitDialogFooter><button type="button">Keep editing</button> <button type="button">Discard</button></span>
</fleet-kit-dialog>`,
  }),
};
