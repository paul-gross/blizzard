import type { Meta, StoryObj } from '@storybook/angular-vite';

import { KitSwatch } from './kit-swatch';

const meta: Meta<KitSwatch> = {
  title: 'Kit/Swatch',
  component: KitSwatch,
  args: { color: 'blue', label: null },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-swatch [color]="color" [label]="label" />`,
  }),
};
export default meta;

type Story = StoryObj<KitSwatch>;

export const PaletteName: Story = {};
export const PaletteNameLabelled: Story = {
  args: { color: 'green', label: 'blizzard' },
};
export const UnknownPaletteName: Story = { args: { color: 'chartreuse' } };
export const HexColor: Story = { args: { color: '#e0662f' } };
export const HexColorLabelled: Story = {
  args: { color: '#e0662f', label: 'winter-workflow' },
};
export const DarkHexColor: Story = {
  args: { color: '#101820', label: 'blizzard-infra' },
};
