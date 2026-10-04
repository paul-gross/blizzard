"""The strict YAML binding of the config codec — the only module under ``src/blizzard`` that imports PyYAML
(``bzh:config-codec``).

Strict is the YAML 1.2 core schema on PyYAML: booleans are ``true``/``false`` only, no timestamp, sexagesimal, or
``yes``/``no`` resolvers, a leading zero is decimal — such scalars stay strings. A duplicate key is refused."""

from __future__ import annotations

import re
from collections.abc import Hashable, Mapping
from typing import Any

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode, ScalarNode

from blizzard.hub.documents.errors import ConfigDecodeError

_CORE_RESOLVERS: tuple[tuple[str, re.Pattern[str], list[str]], ...] = (
    ("tag:yaml.org,2002:null", re.compile(r"^(?:~|null|Null|NULL|)$"), ["~", "n", "N", ""]),
    ("tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$"), list("tf")),
    ("tag:yaml.org,2002:int", re.compile(r"^(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$"), list("-+0123456789")),
    (
        "tag:yaml.org,2002:float",
        re.compile(
            r"^(?:[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$"
        ),
        list("-+0123456789."),
    ),
)


class _StrictLoader(yaml.SafeLoader):
    """``SafeLoader`` on the YAML 1.2 core schema, refusing a duplicate mapping key."""

    yaml_implicit_resolvers: dict[Any, Any] = {}  # noqa: RUF012 - replaces, never mutates, the parent's table

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Hashable, Any]:  # type: ignore[override]
        seen: set[Hashable] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=True)
            if isinstance(key, Hashable):
                if key in seen:
                    raise ConstructorError(None, None, f"duplicate key {key!r}", key_node.start_mark)
                seen.add(key)
        return super().construct_mapping(node, deep)

    def construct_core_int(self, node: ScalarNode) -> int:
        value = str(self.construct_scalar(node))
        if value.startswith(("0o", "0x")):
            return int(value[2:], 8 if value.startswith("0o") else 16)
        return int(value, 10)

    def construct_core_float(self, node: ScalarNode) -> float:
        value = str(self.construct_scalar(node)).lower()
        return float(value.replace(".inf", "inf").replace(".nan", "nan"))


class _StrictDumper(yaml.SafeDumper):
    """``SafeDumper`` that asks the same core-schema resolvers the loader reads with which scalars need quoting."""

    yaml_implicit_resolvers: dict[Any, Any] = {}  # noqa: RUF012 - replaces, never mutates, the parent's table


for _cls in (_StrictLoader, _StrictDumper):
    for _tag, _regexp, _first in _CORE_RESOLVERS:
        _cls.add_implicit_resolver(_tag, _regexp, _first)
_StrictLoader.add_constructor("tag:yaml.org,2002:int", _StrictLoader.construct_core_int)
_StrictLoader.add_constructor("tag:yaml.org,2002:float", _StrictLoader.construct_core_float)


class YamlCodec:
    """Strict YAML: decode and encode round-trip each other."""

    media_types = ("application/yaml", "application/x-yaml", "text/yaml", "text/x-yaml")
    extensions = (".yaml", ".yml")

    def decode(self, data: bytes) -> dict[str, object]:
        loader = _StrictLoader(data)
        try:
            loaded = loader.get_single_data()
        except yaml.MarkedYAMLError as exc:
            mark = exc.problem_mark
            problem = exc.problem or "invalid YAML"
            if mark is None:
                raise ConfigDecodeError(problem) from exc
            raise ConfigDecodeError(problem, line=mark.line + 1, column=mark.column + 1) from exc
        except yaml.YAMLError as exc:
            raise ConfigDecodeError(str(exc)) from exc
        finally:
            loader.dispose()
        if not isinstance(loaded, dict):
            raise ConfigDecodeError("document root must be a mapping")
        return loaded

    def encode(self, document: Mapping[str, object]) -> bytes:
        return yaml.dump(dict(document), Dumper=_StrictDumper, sort_keys=False, encoding="utf-8")


YAML_CODEC = YamlCodec()
