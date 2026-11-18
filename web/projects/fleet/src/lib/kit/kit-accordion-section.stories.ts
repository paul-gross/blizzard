import type { Meta, StoryObj } from '@storybook/angular-vite';
import { fn } from 'storybook/test';

import { moduleMetadata } from '@storybook/angular-vite';

import { KitAccordionGroup } from './kit-accordion-group';
import { KitAccordionSection } from './kit-accordion-section';

const meta: Meta<KitAccordionSection> = {
  title: 'Kit/Accordion section',
  component: KitAccordionSection,
  decorators: [moduleMetadata({ imports: [KitAccordionGroup] })],
  args: {
    expanded: false,
    disabled: false,
    sectionId: 'transcripts',
    expandedChange: fn(),
  },
  render: (args) => ({
    props: args,
    template: `<div fleetKitAccordionGroup><fleet-kit-accordion-section [expanded]="expanded" [disabled]="disabled" [sectionId]="sectionId" (expandedChange)="expandedChange($event)"><span accordionHeader>Transcripts</span><p style="margin: 0; padding: 8px">Three transcripts recorded for this node.</p></fleet-kit-accordion-section></div>`,
  }),
};
export default meta;

type Story = StoryObj<KitAccordionSection>;

export const Collapsed: Story = {};
export const Expanded: Story = { args: { expanded: true } };
export const Disabled: Story = { args: { disabled: true } };
export const WithAside: Story = {
  args: { expanded: true },
  render: (args) => ({
    props: args,
    template: `<div fleetKitAccordionGroup><fleet-kit-accordion-section [expanded]="expanded" [disabled]="disabled" [sectionId]="sectionId" (expandedChange)="expandedChange($event)"><span accordionHeader>Issues</span><a accordionAside href="#">#42</a><p style="margin: 0; padding: 8px">Two open issues reference this chunk.</p></fleet-kit-accordion-section></div>`,
  }),
};
export const Stacked: Story = {
  render: (args) => ({
    props: args,
    template: `<div fleetKitAccordionGroup><fleet-kit-accordion-section [expanded]="expanded" [disabled]="disabled" [sectionId]="sectionId" (expandedChange)="expandedChange($event)"><span accordionHeader>Transcripts</span><p style="margin: 0; padding: 8px">Transcripts body</p></fleet-kit-accordion-section><fleet-kit-accordion-section [expanded]="true" sectionId="artifacts" (expandedChange)="expandedChange($event)"><span accordionHeader>Artifacts</span><p style="margin: 0; padding: 8px">Artifacts body</p></fleet-kit-accordion-section></div>`,
  }),
};
export const LongHeader: Story = {
  render: (args) => ({
    props: args,
    template: `<div fleetKitAccordionGroup><fleet-kit-accordion-section [expanded]="expanded" [disabled]="disabled" [sectionId]="sectionId" (expandedChange)="expandedChange($event)"><span accordionHeader>Transcripts from every attempt this node made before the plan was revised and the chunk was re-minted</span><p style="margin: 0; padding: 8px">Body</p></fleet-kit-accordion-section></div>`,
  }),
};
