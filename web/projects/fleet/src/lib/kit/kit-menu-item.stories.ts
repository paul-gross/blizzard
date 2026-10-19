import { CdkMenuTrigger } from '@angular/cdk/menu';
import {
  moduleMetadata,
  type Meta,
  type StoryObj,
} from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { KitMenuPanel } from './kit-menu';
import { KitMenuItem, KitMenuItemSubtitle } from './kit-menu-item';

/** The host-directive inputs and outputs the class itself does not declare. */
type Args = KitMenuItem & { disabled: boolean; triggered: () => void };

const meta: Meta<Args> = {
  title: 'Kit/Menu item',
  component: KitMenuItem,
  decorators: [
    moduleMetadata({
      imports: [KitMenuPanel, KitMenuItemSubtitle, CdkMenuTrigger],
    }),
  ],
  args: { testid: null, submenu: false, disabled: false, triggered: fn() },
  render: (args) => ({
    props: args,
    template: `
      <fleet-kit-menu-panel style="width: 240px">
        <fleet-kit-menu-item [testid]="testid" [submenu]="submenu" [disabled]="disabled" (triggered)="triggered()">Retry chunk</fleet-kit-menu-item>
        <fleet-kit-menu-item>Stop chunk</fleet-kit-menu-item>
      </fleet-kit-menu-panel>`,
  }),
};
export default meta;

type Story = StoryObj<Args>;

export const Default: Story = {};
export const WithSubtitle: Story = {
  render: (args) => ({
    props: args,
    template: `
      <fleet-kit-menu-panel style="width: 240px">
        <fleet-kit-menu-item [testid]="testid" [submenu]="submenu" [disabled]="disabled" (triggered)="triggered()">Retry chunk<span fleetKitMenuItemSubtitle>from the last node-step</span></fleet-kit-menu-item>
      </fleet-kit-menu-panel>`,
  }),
};
export const Submenu: Story = {
  args: { submenu: true },
  render: (args) => ({
    props: args,
    template: `
      <fleet-kit-menu-panel style="width: 240px">
        <fleet-kit-menu-item [cdkMenuTriggerFor]="sub" [testid]="testid" [submenu]="submenu" [disabled]="disabled" (triggered)="triggered()">Move to</fleet-kit-menu-item>
      </fleet-kit-menu-panel>
      <ng-template #sub>
        <fleet-kit-menu-panel>
          <fleet-kit-menu-item>Top of queue</fleet-kit-menu-item>
          <fleet-kit-menu-item>Bottom of queue</fleet-kit-menu-item>
        </fleet-kit-menu-panel>
      </ng-template>`,
  }),
};
export const Disabled: Story = { args: { disabled: true } };
export const LongLabel: Story = {
  render: (args) => ({
    props: args,
    template: `
      <fleet-kit-menu-panel style="width: 240px">
        <fleet-kit-menu-item [testid]="testid" [submenu]="submenu" [disabled]="disabled" (triggered)="triggered()">Retry the chunk from its last successful node-step on a fresh lease</fleet-kit-menu-item>
      </fleet-kit-menu-panel>`,
  }),
};
