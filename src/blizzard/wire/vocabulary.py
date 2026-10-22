"""Open and declared-closed response vocabulary fields (bzh:open-response-vocabulary).

A response field typed with a vocabulary — a ``StrEnum``, or a ``Literal`` of more than one value — is either open or
declared closed. An open field, ``Annotated[E | Unrecognized, OPEN]``, parses a value outside ``E`` as
:class:`~blizzard.foundation.unrecognized.Unrecognized` and serializes it back raw; its JSON schema stays ``E``'s, with
:data:`OPEN_MARK` beside it so a release's document records which positions its clients tolerate."""

from __future__ import annotations

import types
from dataclasses import dataclass
from typing import Any, Union, get_args, get_origin

from pydantic import GetCoreSchemaHandler, GetJsonSchemaHandler, ValidationError
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import core_schema

from blizzard.foundation.roles import dto
from blizzard.foundation.unrecognized import Unrecognized

#: The OpenAPI extension an open position carries, beside its ``$ref`` or inline ``enum``.
OPEN_MARK = "x-blizzard-open"


class OpenVocabulary:
    """The annotation that parses a vocabulary tolerantly; annotate with its one instance, below."""

    def __get_pydantic_core_schema__(self, source: Any, handler: GetCoreSchemaHandler) -> core_schema.CoreSchema:
        if get_origin(source) not in (Union, types.UnionType) or Unrecognized not in get_args(source):
            raise TypeError(f"an open vocabulary field is typed E | Unrecognized, not {source!r}")
        known = [arg for arg in get_args(source) if arg is not Unrecognized]
        parse_known = handler.generate_schema(known[0] if len(known) == 1 else Union[tuple(known)])  # noqa: UP007

        def tolerate(value: Any, parse: core_schema.ValidatorFunctionWrapHandler) -> Any:
            if isinstance(value, Unrecognized):
                return value
            try:
                return parse(value)
            except ValidationError:
                if isinstance(value, str):
                    return Unrecognized(value)
                raise

        return core_schema.no_info_wrap_validator_function(
            tolerate,
            parse_known,
            serialization=core_schema.plain_serializer_function_ser_schema(_raw, when_used="always"),
        )

    def __get_pydantic_json_schema__(
        self, schema: core_schema.CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return {**handler(schema), OPEN_MARK: True}


def _raw(value: Any) -> Any:
    return value.raw if isinstance(value, Unrecognized) else value


#: Annotate a response vocabulary field open: ``Annotated[E | Unrecognized, OPEN]``.
OPEN = OpenVocabulary()


@dto
@dataclass(frozen=True)
class DeclaredClosed:
    """Annotate a response vocabulary field closed: ``Annotated[E, DeclaredClosed("…")]``. ``because`` says why no
    safe fallback exists — acting without knowing the value would do wrong work — so a gained value stays a break."""

    because: str
