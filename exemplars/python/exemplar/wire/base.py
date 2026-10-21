"""The exemplar package's response-model base — the shape ``blizzard.wire.base.WireModel`` takes for blizzard's own wire."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ResponseModel(BaseModel):
    """A model whose every field is always serialized and never parsed from a request: its exported schema lists
    a defaulted field as required, because the server always sends it."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
