import type { Meta, StoryObj } from '@storybook/angular-vite';
import { moduleMetadata } from '@storybook/angular-vite';

import { KitAccordionGroup } from './kit-accordion-group';
import { KitAccordionSection } from './kit-accordion-section';

const meta: Meta<KitAccordionGroup> = {
  title: 'Kit/Accordion group',
  component: KitAccordionGroup,
  decorators: [moduleMetadata({ imports: [KitAccordionSection] })],
  render: () => ({
    template: `<div fleetKitAccordionGroup>
      <fleet-kit-accordion-section sectionId="one" [expanded]="true"><span accordionHeader>First</span><p style="margin: 0; padding: 8px">First body</p></fleet-kit-accordion-section>
      <fleet-kit-accordion-section sectionId="two"><span accordionHeader>Second</span><p style="margin: 0; padding: 8px">Second body</p></fleet-kit-accordion-section>
      <fleet-kit-accordion-section sectionId="three"><span accordionHeader>Third</span><p style="margin: 0; padding: 8px">Third body</p></fleet-kit-accordion-section>
    </div>`,
  }),
};
export default meta;

type Story = StoryObj<KitAccordionGroup>;

/** Up/Down/Home/End move between headers, Enter and Space toggle. */
export const Default: Story = {};
