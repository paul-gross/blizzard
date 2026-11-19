/** Metadata for an urgency's image mark; the image bytes are read separately. */
export interface UrgencyIcon {
  readonly mediaType: string;
  readonly digest: string;
}

/** A reference to an urgency, resolved against the tenant's urgency set. */
export interface UrgencyReference {
  readonly id: string;
  readonly slug: string;
}

/** The hub's urgency read facts, including its lifecycle and default judgments. */
export interface Urgency {
  readonly id: string;
  readonly slug: string;
  readonly name: string;
  readonly color: string;
  readonly emoji: string | null;
  readonly icon: UrgencyIcon | null;
  readonly retired: boolean;
  readonly default: boolean;
  readonly place: number | null;
  readonly revision: number;
  readonly openChunks: number;
  readonly createdAt: string;
  readonly createdBy: string;
}
