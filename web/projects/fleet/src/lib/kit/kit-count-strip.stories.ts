import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { KitCountStrip, type KitCountCell } from './kit-count-strip';

const TWO: readonly KitCountCell[] = [
  { key: 'delivering', count: 3, caption: 'delivering', testid: 'cell-delivering', selectable: true },
  { key: 'recent', count: 12, caption: 'delivered 48h', testid: 'cell-recent', selectable: true },
];

const FIVE: readonly KitCountCell[] = [
  { key: 'needs', count: 2, caption: 'needs you', testid: 'cell-needs', selectable: true, countColor: 'var(--amber)' },
  { key: 'running', count: 5, caption: 'running', testid: 'cell-running', selectable: true, countColor: 'var(--cyan)' },
  { key: 'ready', count: 8, caption: 'ready', testid: 'cell-ready', selectable: true },
  { key: 'done', count: 14, caption: 'done (48h)', testid: 'cell-done', selectable: true, countColor: 'var(--green)' },
  { key: 'runners', count: '3/4', caption: 'runners up', testid: 'cell-runners', selectable: false },
];

const meta: Meta<KitCountStrip> = {
  title: 'Kit/Count strip',
  component: KitCountStrip,
  args: { cells: TWO, selected: null, testid: null, cellClick: fn() },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-count-strip [cells]="cells" [selected]="selected" [testid]="testid" (cellClick)="cellClick($event)" />`,
  }),
};
export default meta;

type Story = StoryObj<KitCountStrip>;

export const TwoNoneSelected: Story = {};
export const TwoOneSelected: Story = { args: { selected: 'recent' } };
export const FiveMixed: Story = { args: { cells: FIVE, selected: 'running' } };
export const TwoPhone: Story = {
  args: { selected: 'delivering' },
  globals: { viewport: { value: 'phone', isRotated: false } },
};
export const TwoPhoneSmall: Story = {
  args: { selected: 'delivering' },
  globals: { viewport: { value: 'phone-sm', isRotated: false } },
};
export const FivePhone: Story = {
  args: { cells: FIVE, selected: 'needs' },
  globals: { viewport: { value: 'phone', isRotated: false } },
};
export const FivePhoneSmall: Story = {
  args: { cells: FIVE, selected: 'needs' },
  globals: { viewport: { value: 'phone-sm', isRotated: false } },
};
