import { moduleMetadata, type Meta, type StoryObj } from '@storybook/angular-vite';

import { KitGridFixture } from './kit-grid.fixture';
import { KitGridWidget } from './kit-grid-widget';

const meta: Meta<KitGridWidget> = {
  title: 'Kit/Grid widget',
  component: KitGridWidget,
  decorators: [moduleMetadata({ imports: [KitGridFixture] })],
  render: () => ({ template: '<fleet-kit-grid-fixture />' }),
};
export default meta;

type Story = StoryObj<KitGridWidget>;

/** Two rows: text cells, an enabled then a disabled select and button. One Tab stop, arrows move, Enter or Space works a control. */
export const Default: Story = {};
