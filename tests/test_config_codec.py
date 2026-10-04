"""The config codec seam (unit tier) — the strict YAML binding, the JSON binding, and the registry."""

from __future__ import annotations

import pytest

from blizzard.hub.documents.codec import (
    CONFIG_CODECS,
    YAML_CODEC,
    ConfigDecodeError,
    accepted_extensions,
    codec_for_media_type,
    codec_for_path,
)

pytestmark = pytest.mark.unit

_JSON_CODEC = codec_for_media_type("application/json")


def test_yaml_scalars_that_yaml_1_1_resolves_stay_strings() -> None:
    decoded = YAML_CODEC.decode(b"a: yes\nb: no\nc: on\nd: 2001-01-01\ne: 1:30\nf: Y\n")

    assert decoded == {"a": "yes", "b": "no", "c": "on", "d": "2001-01-01", "e": "1:30", "f": "Y"}


def test_yaml_core_schema_scalars_resolve() -> None:
    decoded = YAML_CODEC.decode(b"t: true\nf: false\nn: null\ni: 017\nh: 0x1F\no: 0o17\nx: 1e3\ny: 2.5\n")

    assert decoded == {"t": True, "f": False, "n": None, "i": 17, "h": 31, "o": 15, "x": 1000.0, "y": 2.5}


def test_a_capitalized_boolean_is_a_string() -> None:
    assert YAML_CODEC.decode(b"a: True\n") == {"a": "True"}


def test_a_duplicate_key_is_refused_with_its_position() -> None:
    with pytest.raises(ConfigDecodeError, match=r"duplicate key 'a' \(line 2, column 1\)") as raised:
        YAML_CODEC.decode(b"a: 1\na: 2\n")

    assert (raised.value.line, raised.value.column) == (2, 1)


def test_a_duplicate_key_after_a_merge_key_is_refused() -> None:
    with pytest.raises(ConfigDecodeError, match=r"duplicate key 'a' \(line 5, column 3\)"):
        YAML_CODEC.decode(b"base: &base {z: 0}\nmerged:\n  <<: *base\n  a: 1\n  a: 2\n")


def test_malformed_syntax_carries_the_parser_position() -> None:
    with pytest.raises(ConfigDecodeError) as raised:
        YAML_CODEC.decode(b"a: [1, 2\nb: 3\n")

    assert raised.value.line is not None


@pytest.mark.parametrize("document", [b"", b"- a\n- b\n", b"just a scalar\n"])
def test_a_root_that_is_not_a_mapping_is_refused(document: bytes) -> None:
    with pytest.raises(ConfigDecodeError, match="mapping"):
        YAML_CODEC.decode(document)


@pytest.mark.parametrize("codec", CONFIG_CODECS, ids=lambda codec: codec.extensions[0])
def test_encode_round_trips_through_decode(codec) -> None:  # type: ignore[no-untyped-def]
    document: dict[str, object] = {
        "yes": "yes",
        "sci": "1e3",
        "octal": "0o17",
        "leading_zero": "017",
        "date": "2001-01-01",
        "bool_word": "true",
        "null_word": "null",
        "empty": "",
        "colon": "x: y",
        "multi": "line one\nline two\n",
        "nested": {"list": [1, 2.5, None, True, "no"]},
    }

    assert codec.decode(codec.encode(document)) == document


def test_encode_keeps_the_mapping_order() -> None:
    assert YAML_CODEC.encode({"z": 1, "a": 2}).decode().splitlines() == ["z: 1", "a: 2"]


def test_json_decodes_json_and_refuses_a_duplicate_key() -> None:
    assert _JSON_CODEC is not None
    assert _JSON_CODEC.decode(b'{"a": {"b": [1, "yes"]}}') == {"a": {"b": [1, "yes"]}}
    with pytest.raises(ConfigDecodeError, match="duplicate key"):
        _JSON_CODEC.decode(b'{"a": 1, "a": 2}')


def test_json_encodes_json() -> None:
    assert _JSON_CODEC is not None
    assert _JSON_CODEC.encode({"a": "é"}).decode().replace(" ", "").replace("\n", "") == '{"a":"é"}'


@pytest.mark.parametrize(
    ("media_type", "extensions"),
    [
        ("application/yaml", (".yaml", ".yml")),
        ("text/x-yaml", (".yaml", ".yml")),
        ("application/json", (".json",)),
        ("Application/JSON; charset=utf-8", (".json",)),
    ],
)
def test_registry_resolves_a_media_type_to_the_binding_declaring_it(
    media_type: str, extensions: tuple[str, ...]
) -> None:
    codec = codec_for_media_type(media_type)

    assert codec is not None
    assert codec.extensions == extensions


@pytest.mark.parametrize(
    ("path", "media_type"),
    [("graph.yaml", "application/yaml"), ("a/b/graph.YML", "application/yaml"), ("graph.json", "application/json")],
)
def test_registry_resolves_a_path_by_its_extension(path: str, media_type: str) -> None:
    assert codec_for_path(path) is codec_for_media_type(media_type)


def test_registry_answers_none_for_what_no_binding_declares() -> None:
    assert codec_for_media_type("text/plain") is None
    assert codec_for_path("notes.txt") is None
    assert codec_for_path("graph") is None


def test_accepted_extensions_lists_every_declared_extension() -> None:
    assert accepted_extensions() == (".yaml", ".yml", ".json")


def test_json_reads_tab_whitespace_and_surrogate_pair_escapes() -> None:
    assert _JSON_CODEC is not None

    decoded = _JSON_CODEC.decode(b'{\n\t"a": 1,\n\t"b": "\\ud83d\\ude00"\n}')

    assert decoded == {"a": 1, "b": "\U0001f600"}
    assert isinstance(decoded["b"], str)
    decoded["b"].encode("utf-8")  # type: ignore[union-attr]


def test_json_reports_syntax_errors_with_position_and_refuses_a_non_mapping_root() -> None:
    assert _JSON_CODEC is not None
    with pytest.raises(ConfigDecodeError) as raised:
        _JSON_CODEC.decode(b'{\n "a": }')
    assert (raised.value.line, raised.value.column) == (2, 7)
    with pytest.raises(ConfigDecodeError, match="mapping"):
        _JSON_CODEC.decode(b"[1]")


def test_yaml_merge_keys_still_merge() -> None:
    decoded = YAML_CODEC.decode(b"base: &x {p: 1, q: 2}\nc:\n  <<: *x\n  p: 3\n")

    assert decoded["c"] == {"p": 3, "q": 2}


def test_a_literal_merge_word_round_trips() -> None:
    assert YAML_CODEC.decode(YAML_CODEC.encode({"k": "<<"})) == {"k": "<<"}


def test_decode_error_message_and_problem_without_a_full_position() -> None:
    assert str(ConfigDecodeError("bad")) == "bad"
    assert str(ConfigDecodeError("bad", line=3)) == "bad"
    assert str(ConfigDecodeError("bad", column=3)) == "bad"
    error = ConfigDecodeError("bad", line=3, column=4)
    assert (error.problem, str(error)) == ("bad", "bad (line 3, column 4)")


def test_yaml_special_floats_decode_in_every_case_form() -> None:
    decoded = YAML_CODEC.decode(b"a: .inf\nb: -.inf\nc: .INF\nd: +.Inf\ne: .nan\nf: .NaN\ng: .NAN\n")

    assert decoded["a"] == float("inf")
    assert decoded["b"] == float("-inf")
    assert decoded["c"] == decoded["d"] == float("inf")
    assert all(decoded[key] != decoded[key] for key in "efg")
