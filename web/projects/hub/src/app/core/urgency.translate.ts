import type { Urgency, UrgencyRef, UrgencyReference, UrgencyView } from 'fleet';

export function fromUrgencyRef(wire: UrgencyRef): UrgencyReference {
  return { id: wire.urgency_id, slug: wire.slug };
}

export function fromUrgencyView(wire: UrgencyView): Urgency {
  return {
    id: wire.urgency_id,
    slug: wire.slug,
    name: wire.name,
    color: wire.color,
    emoji: wire.emoji,
    icon: wire.icon === null ? null : { mediaType: wire.icon.media_type, digest: wire.icon.digest },
    retired: wire.retired,
    default: wire.default,
    place: wire.place,
    revision: wire.revision,
    openChunks: wire.open_chunks,
    createdAt: wire.created_at,
    createdBy: wire.created_by,
  };
}
