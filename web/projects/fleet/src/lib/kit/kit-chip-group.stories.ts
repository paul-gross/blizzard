import type { Meta, StoryObj } from '@storybook/angular-vite';
import { moduleMetadata } from '@storybook/angular-vite';
import { fn } from 'storybook/test';
import { KitChipGroup } from './kit-chip-group';
import { KitChipOptionDirective } from './kit-chip-option';
import { KitChip } from './kit-chips';

const meta: Meta<KitChipGroup> = {
  title: 'Kit/Chip Group',
  component: KitChipGroup,
  decorators: [moduleMetadata({ imports: [KitChipGroup, KitChipOptionDirective, KitChip] })],
  args: { value: null, hasSelection: true, valueChange: fn() },
  render: (args) => ({
    props: args,
    template: `<div fleetKitChipGroup orientation="horizontal" [selectionMode]="hasSelection ? 'follow' : 'explicit'" aria-label="Project" [value]="value" [hasSelection]="hasSelection" (valueChange)="value = $event; hasSelection = true; valueChange($event)">
      <fleet-kit-chip fleetKitChipOption [value]="null" label="All">All</fleet-kit-chip>
      <fleet-kit-chip fleetKitChipOption value="alpha" label="Alpha">Alpha</fleet-kit-chip>
      <fleet-kit-chip fleetKitChipOption value="disabled" label="Disabled" [disabled]="true">Disabled</fleet-kit-chip>
    </div>`,
  }),
};
export default meta;
type Story = StoryObj<KitChipGroup>;
export const Projected: Story = {};
export const NoSelection: Story = { args: { hasSelection: false } };
