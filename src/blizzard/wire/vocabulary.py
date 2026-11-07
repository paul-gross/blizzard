"""Open and declared-closed response vocabulary fields (bzh:open-response-vocabulary).

A response field typed with a vocabulary — a ``StrEnum``, or a ``Literal`` of more than one value — is either open or
declared closed. An open field, ``Annotated[E | Unrecognized, OPEN]``, parses a value outside ``E`` as
:class:`~blizzard.foundation.unrecognized.Unrecognized` and serializes it back raw; its JSON schema stays ``E``'s, with
:data:`OPEN_MARK` beside it so a release's document records which positions its clients tolerate."""

from __future__ import annotations

import types
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, TypeAliasType, Union, cast, get_args, get_origin

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
        source = getattr(source, "__value__", source) if isinstance(source, TypeAliasType) else source
        if get_origin(source) not in (Union, types.UnionType) or Unrecognized not in get_args(source):
            raise TypeError(f"an open vocabulary field is typed E | Unrecognized, not {source!r}")
        known = [arg for arg in get_args(source) if arg is not Unrecognized]
        parse_known = self._known_schema(known[0] if len(known) == 1 else Union[tuple(known)], handler)  # noqa: UP007

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

    def _known_schema(self, known: Any, handler: GetCoreSchemaHandler) -> core_schema.CoreSchema:
        return handler.generate_schema(known)

    def __get_pydantic_json_schema__(
        self, schema: core_schema.CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return {**handler(schema), OPEN_MARK: True}


def _raw(value: Any) -> Any:
    return value.raw if isinstance(value, Unrecognized) else value


#: Annotate a response vocabulary field open: ``Annotated[E | Unrecognized, OPEN]``.
OPEN = OpenVocabulary()


def open_value[E](known: type[E] | TypeAliasType, name: str) -> E | Unrecognized:
    """Read a stored vocabulary name as ``E | Unrecognized`` — the member when ``name`` is in the vocabulary, else the
    unknown value carried raw. ``known`` is the vocabulary: a ``StrEnum`` or a ``type`` alias of a ``Literal``."""
    if isinstance(known, TypeAliasType):
        members: tuple[Any, ...] = get_args(known.__value__)
        return cast("E", name) if name in members else Unrecognized(name)
    try:
        return cast("Callable[[str], E]", known)(name)
    except ValueError:
        return Unrecognized(name)


@dto
@dataclass(frozen=True)
class DeclaredClosed:
    """Annotate a response vocabulary field closed: ``Annotated[E, DeclaredClosed("…")]``. ``because`` says why no
    safe fallback exists — acting without knowing the value would do wrong work — so a gained value stays a break."""

    because: str


class InlineVocabulary:
    """The annotation that writes an enum's values inline at its field instead of ``$ref``-ing a component, so a
    position a document has always carried inline stays so. ``title`` is the field's title, which the inline schema
    carries in place of the component's."""

    def __init__(self, title: str) -> None:
        self.title = title

    def __get_pydantic_json_schema__(
        self, schema: core_schema.CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        resolved = handler.resolve_ref_schema(handler(schema))
        return {
            **{key: value for key, value in resolved.items() if key not in ("title", "description")},
            "title": self.title,
        }


class ToleratedRequestVocabulary(OpenVocabulary):
    """The annotation that reads a request vocabulary tolerantly, for a field whose server reads every unknown value as
    a declared default. A value outside the vocabulary parses as an unrecognized one and serializes back raw, and the
    field's JSON schema stays a plain string: a request property that moved from string to an enum would read as
    narrowed to every older client. ``title`` is the field's title."""

    def __init__(self, title: str) -> None:
        self.title = title

    def _known_schema(self, known: Any, handler: GetCoreSchemaHandler) -> core_schema.CoreSchema:
        # An enum parses by its constructor, not by an enum schema, so no component enters a document for it.
        if isinstance(known, type) and issubclass(known, Enum):
            return core_schema.no_info_plain_validator_function(known)
        return handler.generate_schema(known)

    def __get_pydantic_json_schema__(
        self, schema: core_schema.CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return {"type": "string", "title": self.title}
