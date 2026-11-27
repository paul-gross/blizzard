import { moduleMetadata, type Meta, type StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { KitSelectList } from './kit-select-list';
import { KitSelectRow } from './kit-select-row';

const FEW = ['plan.md', 'review-findings.json', 'retrospective.md', 'branch'];
const MANY = Array.from({ length: 40 }, (_, i) => `artifact-${String(i + 1).padStart(2, '0')}.md`);

const meta: Meta<KitSelectList> = {
  title: 'Kit/Select list',
  component: KitSelectList,
  decorators: [moduleMetadata({ imports: [KitSelectRow] })],
  args: { value: null, ariaLabel: 'Artifacts', valueChange: fn() },
  render: (args) => ({
    props: { ...args, names: FEW },
    template: `
      <div fleetKitSelectList selectionMode="explicit" [softDisabled]="false" [wrap]="false" focusMode="activedescendant" [ariaLabel]="ariaLabel" [value]="value" (valueChange)="valueChange($event)" style="max-width: 320px">
        @for (row of names; track row) {
          <fleet-kit-select-row [value]="row" [label]="row">{{ row }}</fleet-kit-select-row>
        }
      </div>`,
  }),
};
export default meta;

type Story = StoryObj<KitSelectList>;

export const Default: Story = {};
export const Selected: Story = { args: { value: 'review-findings.json' } };
export const Overflowing: Story = {
  args: { value: 'artifact-07.md' },
  render: (args) => ({
    props: { ...args, names: MANY },
    template: `
      <div style="max-width: 320px; height: 240px; overflow: auto">
        <div fleetKitSelectList selectionMode="explicit" [softDisabled]="false" [wrap]="false" focusMode="activedescendant" [ariaLabel]="ariaLabel" [value]="value" (valueChange)="valueChange($event)">
          @for (row of names; track row) {
            <fleet-kit-select-row [value]="row" [label]="row">{{ row }}</fleet-kit-select-row>
          }
        </div>
      </div>`,
  }),
};
export const Empty: Story = {
  render: (args) => ({
    props: args,
    template: `<div fleetKitSelectList selectionMode="explicit" [softDisabled]="false" [wrap]="false" focusMode="activedescendant" [ariaLabel]="ariaLabel" [value]="value" style="max-width: 320px"></div>`,
  }),
};
export const DisabledRows: Story = {
  render: (args) => ({
    props: { ...args, names: FEW },
    template: `
      <div fleetKitSelectList selectionMode="explicit" [softDisabled]="false" [wrap]="false" focusMode="activedescendant" [ariaLabel]="ariaLabel" [value]="value" (valueChange)="valueChange($event)" style="max-width: 320px">
        @for (row of names; track row; let i = $index) {
          <fleet-kit-select-row [value]="row" [label]="row" [disabled]="i % 2 === 1">{{ row }}</fleet-kit-select-row>
        }
      </div>`,
  }),
};
export const Bare: Story = {
  render: (args) => ({
    props: { ...args, names: FEW },
    template: `
      <style>[role='listbox']:focus-visible fleet-kit-select-row[data-active='true'] { outline: 1px solid var(--cyan); outline-offset: -2px; }</style>
      <div fleetKitSelectList selectionMode="explicit" [softDisabled]="false" [wrap]="false" focusMode="activedescendant" [ariaLabel]="ariaLabel" [value]="value" (valueChange)="valueChange($event)" style="max-width: 320px">
        @for (row of names; track row) {
          <fleet-kit-select-row bare [value]="row" [label]="row">{{ row }}</fleet-kit-select-row>
        }
      </div>`,
  }),
};
export const Phone: Story = { globals: { viewport: { value: 'phone' } }, args: { value: 'plan.md' } };
export const PhoneSmall: Story = { globals: { viewport: { value: 'phone-sm' } }, args: { value: 'plan.md' } };
