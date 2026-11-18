import { moduleMetadata, type Meta, type StoryObj } from '@storybook/angular-vite';

import { KitGridFixture } from './kit-grid.fixture';
import { KitGridRow } from './kit-grid-row';

const meta: Meta<KitGridRow> = {
  title: 'Kit/Grid row',
  component: KitGridRow,
  decorators: [moduleMetadata({ imports: [KitGridFixture] })],
  render: () => ({ template: '<fleet-kit-grid-fixture />' }),
};
export default meta;

type Story = StoryObj<KitGridRow>;

/** Two rows: text cells, an enabled then a disabled select and button. One Tab stop, arrows move, Enter or Space works a control. */
export const Default: Story = {};
