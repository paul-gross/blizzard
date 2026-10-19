import { CdkMenuTrigger } from '@angular/cdk/menu';
import {
  moduleMetadata,
  type Meta,
  type StoryObj,
} from '@storybook/angular-vite';

import { KitAvatar } from './kit-avatar';
import { KitMenu, KitMenuPanel } from './kit-menu';
import { KitMenuItem, KitMenuItemRadio } from './kit-menu-item';

const meta: Meta<KitMenu> = {
  title: 'Kit/Menu',
  component: KitMenu,
  decorators: [
    moduleMetadata({
      imports: [
        KitMenuPanel,
        KitMenuItem,
        KitMenuItemRadio,
        KitAvatar,
        CdkMenuTrigger,
      ],
    }),
  ],
  args: { ariaLabel: 'Chunk actions', testid: null },
  argTypes: { menu: { table: { disable: true }, control: false } },
  render: (args) => ({
    props: args,
    template: `
      <fleet-kit-menu [menu]="m" [ariaLabel]="ariaLabel" [testid]="testid" />
      <ng-template #m>
        <fleet-kit-menu-panel>
          <fleet-kit-menu-item>Retry chunk</fleet-kit-menu-item>
          <fleet-kit-menu-item>Stop chunk</fleet-kit-menu-item>
          <hr />
          <fleet-kit-menu-item-radio [checked]="true">r-claude</fleet-kit-menu-item-radio>
          <fleet-kit-menu-item-radio>r-chatgpt</fleet-kit-menu-item-radio>
          <hr />
          <fleet-kit-menu-item [cdkMenuTriggerFor]="sub" [submenu]="true">Move to</fleet-kit-menu-item>
        </fleet-kit-menu-panel>
      </ng-template>
      <ng-template #sub>
        <fleet-kit-menu-panel>
          <fleet-kit-menu-item>Top of queue</fleet-kit-menu-item>
          <fleet-kit-menu-item>Bottom of queue</fleet-kit-menu-item>
        </fleet-kit-menu-panel>
      </ng-template>`,
  }),
};
export default meta;

type Story = StoryObj<KitMenu>;

/** Closed: open the panel, and its submenu, by hand. */
export const DefaultTrigger: Story = {};
export const AvatarTrigger: Story = {
  render: (args) => ({
    props: args,
    template: `
      <fleet-kit-menu [menu]="m" [ariaLabel]="ariaLabel" [testid]="testid"><fleet-kit-avatar trigger /></fleet-kit-menu>
      <ng-template #m>
        <fleet-kit-menu-panel>
          <fleet-kit-menu-item>Profile</fleet-kit-menu-item>
          <fleet-kit-menu-item>Sign out</fleet-kit-menu-item>
        </fleet-kit-menu-panel>
      </ng-template>`,
  }),
};
