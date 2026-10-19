import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { KitAccordionSection } from './kit-accordion-section';

const meta: Meta<KitAccordionSection> = {
  title: 'Kit/Accordion section',
  component: KitAccordionSection,
  args: {
    expanded: false,
    sectionId: 'transcripts',
    expandedChange: fn(),
  },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-accordion-section [expanded]="expanded" [sectionId]="sectionId" (expandedChange)="expandedChange($event)"><span accordionHeader>Transcripts</span><p style="margin: 0; padding: 8px">Three transcripts recorded for this node.</p></fleet-kit-accordion-section>`,
  }),
};
export default meta;

type Story = StoryObj<KitAccordionSection>;

export const Collapsed: Story = {};
export const Expanded: Story = { args: { expanded: true } };
export const WithAside: Story = {
  args: { expanded: true },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-accordion-section [expanded]="expanded" [sectionId]="sectionId" (expandedChange)="expandedChange($event)"><span accordionHeader>Issues</span><a accordionAside href="#">#42</a><p style="margin: 0; padding: 8px">Two open issues reference this chunk.</p></fleet-kit-accordion-section>`,
  }),
};
export const Stacked: Story = {
  render: (args) => ({
    props: args,
    template: `<fleet-kit-accordion-section [expanded]="expanded" [sectionId]="sectionId" (expandedChange)="expandedChange($event)"><span accordionHeader>Transcripts</span><p style="margin: 0; padding: 8px">Transcripts body</p></fleet-kit-accordion-section><fleet-kit-accordion-section [expanded]="true" sectionId="artifacts" (expandedChange)="expandedChange($event)"><span accordionHeader>Artifacts</span><p style="margin: 0; padding: 8px">Artifacts body</p></fleet-kit-accordion-section>`,
  }),
};
export const LongHeader: Story = {
  render: (args) => ({
    props: args,
    template: `<fleet-kit-accordion-section [expanded]="expanded" [sectionId]="sectionId" (expandedChange)="expandedChange($event)"><span accordionHeader>Transcripts from every attempt this node made before the plan was revised and the chunk was re-minted</span><p style="margin: 0; padding: 8px">Body</p></fleet-kit-accordion-section>`,
  }),
};
