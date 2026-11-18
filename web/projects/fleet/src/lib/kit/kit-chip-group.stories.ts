import type { Meta, StoryObj } from '@storybook/angular-vite';
import { moduleMetadata } from '@storybook/angular-vite';
import { fn } from 'storybook/test';
import { KitChipGroup } from './kit-chip-group';
import { KitChipOptionDirective } from './kit-chip-option';
import { KitChip } from './kit-chips';

type ChipGroupArgs = KitChipGroup & { disabled: boolean; orientation: 'horizontal' | 'vertical'; selectionMode: 'follow' | 'explicit' };

const meta: Meta<ChipGroupArgs> = {
  title: 'Kit/Chip Group',
  component: KitChipGroup,
  decorators: [moduleMetadata({ imports: [KitChipGroup, KitChipOptionDirective, KitChip] })],
  args: { value: null, hasSelection: true, disabled: false, orientation: 'horizontal', selectionMode: 'follow', valueChange: fn() },
  argTypes: {
    orientation: { control: 'select', options: ['horizontal', 'vertical'] },
    selectionMode: { control: 'select', options: ['follow', 'explicit'] },
  },
  render: (args) => ({
    props: args,
    template: `<div fleetKitChipGroup [orientation]="orientation" [disabled]="disabled" [selectionMode]="selectionMode" aria-label="Project" [value]="value" [hasSelection]="hasSelection" (valueChange)="value = $event; hasSelection = true; valueChange($event)">
      <fleet-kit-chip fleetKitChipOption [value]="null" label="All">All</fleet-kit-chip>
      <fleet-kit-chip fleetKitChipOption value="alpha" label="Alpha">Alpha</fleet-kit-chip>
      <fleet-kit-chip fleetKitChipOption value="disabled" label="Disabled" [disabled]="true">Disabled</fleet-kit-chip>
    </div>`,
  }),
};
export default meta;
type Story = StoryObj<ChipGroupArgs>;
export const Projected: Story = {};
export const NoSelection: Story = { args: { hasSelection: false, selectionMode: 'explicit' } };

export const Disabled: Story = { args: { disabled: true } };
export const Vertical: Story = { args: { orientation: 'vertical' } };
export const ExplicitSelection: Story = { args: { selectionMode: 'explicit' } };
