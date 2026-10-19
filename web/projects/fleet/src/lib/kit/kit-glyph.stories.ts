import type { Meta, StoryObj } from '@storybook/angular-vite';

import { KitGlyph } from './kit-glyph';

const meta: Meta<KitGlyph> = {
  title: 'Kit/Glyph',
  component: KitGlyph,
  args: { glyph: 'pause', label: 'Paused' },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-glyph [glyph]="glyph" [label]="label" />`,
  }),
};
export default meta;

type Story = StoryObj<KitGlyph>;

export const Pause: Story = {};
export const Stop: Story = { args: { glyph: 'stop', label: 'Stopped' } };
export const Coloured: Story = {
  render: (args) => ({
    props: args,
    template: `<span style="color: var(--amber)"><fleet-kit-glyph [glyph]="glyph" [label]="label" /></span>`,
  }),
};
export const Large: Story = {
  render: (args) => ({
    props: args,
    template: `<span style="--kit-glyph-size: 32px"><fleet-kit-glyph [glyph]="glyph" [label]="label" /></span>`,
  }),
};
