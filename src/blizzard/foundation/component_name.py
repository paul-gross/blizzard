"""An enum's OpenAPI component name, when it differs from its Python name."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import GetCoreSchemaHandler, GetJsonSchemaHandler
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import core_schema


class ComponentNamed:
    """Mixin for an enum that a wire model's documents name differently from the class: the schema component, and
    its title, read ``__component_name__``. The name keeps a vocabulary's published component stable while its one
    definition lives under the name the code gives it."""

    #: The name the enum's component and title carry in a generated document. A dunder, so an enum body can set it
    #: without it becoming a member.
    __component_name__: ClassVar[str]

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: GetCoreSchemaHandler) -> core_schema.CoreSchema:
        schema = handler(source)
        if schema["type"] == "enum":
            schema["ref"] = f"{cls.__module__}.{cls.__component_name__}:{id(cls)}"
        return schema

    @classmethod
    def __get_pydantic_json_schema__(
        cls, schema: core_schema.CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        resolved = handler.resolve_ref_schema(handler(schema))
        resolved["title"] = cls.__component_name__
        return resolved
