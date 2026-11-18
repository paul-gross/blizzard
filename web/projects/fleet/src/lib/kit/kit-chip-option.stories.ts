import type { Meta, StoryObj } from '@storybook/angular-vite';
import { moduleMetadata } from '@storybook/angular-vite';
import { KitChipGroup } from './kit-chip-group';
import { KitChipOptionDirective } from './kit-chip-option';
import { KitChip } from './kit-chips';

const meta: Meta<KitChipOptionDirective> = {
  title: 'Kit/Chip Option',
  component: KitChipOptionDirective,
  decorators: [moduleMetadata({ imports: [KitChipGroup, KitChipOptionDirective, KitChip] })],
  render: () => ({ template: `<div fleetKitChipGroup orientation="horizontal" aria-label="Status" value="ready">
    <fleet-kit-chip fleetKitChipOption value="ready" label="Ready">Ready</fleet-kit-chip>
    <fleet-kit-chip fleetKitChipOption value="blocked" label="Blocked" [disabled]="true">Blocked</fleet-kit-chip>
  </div>` }),
};
export default meta;
type Story = StoryObj<KitChipOptionDirective>;
export const SelectedAndDisabled: Story = {};
