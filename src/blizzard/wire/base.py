"""The base every response-only wire model subclasses.

A response model's every field reaches the wire, so its exported schema lists a defaulted field as
required: the generated client types it as always present, and no caller re-derives what the server
already decided (``bzh:wire-sent-is-required``). A model a request body reaches, an SSE payload, or a
model a route serves with an exclude flag does not subclass this — each is sometimes sent without a
field, or parsed with one omitted."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class WireModel(BaseModel):
    """A model whose every field is always serialized and never parsed from a request."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
