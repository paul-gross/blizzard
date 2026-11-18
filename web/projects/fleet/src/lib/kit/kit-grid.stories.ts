import { moduleMetadata, type Meta, type StoryObj } from '@storybook/angular-vite';

import { KitGridFixture } from './kit-grid.fixture';
import { KitGrid } from './kit-grid';

const meta: Meta<KitGrid> = {
  title: 'Kit/Grid',
  component: KitGrid,
  decorators: [moduleMetadata({ imports: [KitGridFixture] })],
  render: () => ({ template: '<fleet-kit-grid-fixture />' }),
};
export default meta;

type Story = StoryObj<KitGrid>;

/** Two rows: text cells, an enabled then a disabled select and button. One Tab stop, arrows move, Enter or Space works a control. */
export const Default: Story = {};
