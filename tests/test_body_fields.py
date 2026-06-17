"""Tests for request-body field extraction and the top-level body convention.

Covers issue #9: the tool schema must advertise the real top-level body fields
(not an opaque ``body: object`` wrapper), the loader must resolve ``$ref`` /
``allOf`` schemas while degrading safely on malformed specs, and the dispatcher
must unwrap a lone ``{"body": ...}`` so old-convention callers still succeed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from thousand_eyes_mcp.loader import (
    OperationSpec,
    SpecLoader,
    ToolGroup,
    _body_root_type,
    _parse_request_body,
)
from thousand_eyes_mcp.tools import _BODY_HINT, _build_description, _format_body


def _names(fields: list) -> set[str]:
    return {f.name for f in fields}


# ---------------------------------------------------------------------------
# loader._parse_request_body
# ---------------------------------------------------------------------------


def test_inline_object_schema_extracts_fields_and_required() -> None:
    op = {
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "ruleName": {"type": "string"},
                            "severity": {"type": "integer"},
                        },
                        "required": ["ruleName"],
                    }
                }
            }
        }
    }
    fields = _parse_request_body(op, {})
    assert _names(fields) == {"ruleName", "severity"}
    by_name = {f.name: f for f in fields}
    assert by_name["ruleName"].required is True
    assert by_name["ruleName"].location == "body"
    assert by_name["ruleName"].type == "string"
    assert by_name["severity"].type == "integer"
    assert by_name["severity"].required is False


def test_ref_to_component_schema_is_resolved() -> None:
    schemas = {
        "AlertRule": {
            "type": "object",
            "properties": {"ruleName": {"type": "string"}, "expression": {"type": "string"}},
            "required": ["ruleName"],
        }
    }
    op = {
        "requestBody": {
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AlertRule"}}}
        }
    }
    fields = _parse_request_body(op, schemas)
    assert _names(fields) == {"ruleName", "expression"}
    assert {f.name for f in fields if f.required} == {"ruleName"}


def test_allof_with_nested_ref_merges_fields() -> None:
    schemas = {
        "Base": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
    }
    op = {
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {
                        "allOf": [
                            {"$ref": "#/components/schemas/Base"},
                            {
                                "type": "object",
                                "properties": {"name": {"type": "string"}},
                                "required": ["name"],
                            },
                        ]
                    }
                }
            }
        }
    }
    fields = _parse_request_body(op, schemas)
    assert _names(fields) == {"id", "name"}
    assert {f.name for f in fields if f.required} == {"id", "name"}


def test_cyclic_ref_degrades_without_crashing() -> None:
    # A references B references A — must not recurse infinitely.
    schemas = {
        "A": {
            "allOf": [{"$ref": "#/components/schemas/B"}],
            "properties": {"a": {"type": "string"}},
        },
        "B": {
            "allOf": [{"$ref": "#/components/schemas/A"}],
            "properties": {"b": {"type": "string"}},
        },
    }
    op = {
        "requestBody": {
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/A"}}}
        }
    }
    fields = _parse_request_body(op, schemas)
    # Both reachable own-properties collected; the cycle is simply cut.
    assert _names(fields) == {"a", "b"}


def test_star_media_fallback_when_no_json() -> None:
    op = {
        "requestBody": {
            "content": {
                "*/*": {"schema": {"type": "object", "properties": {"blob": {"type": "string"}}}}
            }
        }
    }
    fields = _parse_request_body(op, {})
    assert _names(fields) == {"blob"}


def test_first_media_used_when_only_exotic_present() -> None:
    op = {
        "requestBody": {
            "content": {
                "application/vnd.te+json": {
                    "schema": {"type": "object", "properties": {"x": {"type": "string"}}}
                }
            }
        }
    }
    assert _names(_parse_request_body(op, {})) == {"x"}


def test_readonly_fields_excluded() -> None:
    """Server-managed readOnly fields must not be advertised as writable —
    both inline readOnly and readOnly carried on a $ref target (e.g. _links)."""
    schemas = {
        "Links": {"type": "object", "readOnly": True, "properties": {"self": {"type": "string"}}},
    }
    op = {
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "ruleName": {"type": "string"},
                            "createdBy": {"type": "string", "readOnly": True},
                            "_links": {"$ref": "#/components/schemas/Links"},
                        },
                    }
                }
            }
        }
    }
    fields = _parse_request_body(op, schemas)
    assert _names(fields) == {"ruleName"}


def test_field_type_resolves_through_ref() -> None:
    """A property $ref-ing a string/enum component reports its real type, not
    a blanket 'object'."""
    schemas = {"Severity": {"type": "string", "enum": ["INFO", "CRITICAL"]}}
    op = {
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {"severity": {"$ref": "#/components/schemas/Severity"}},
                    }
                }
            }
        }
    }
    fields = _parse_request_body(op, schemas)
    assert {f.name: f.type for f in fields} == {"severity": "string"}


def test_descriptions_and_defaults_carried() -> None:
    op = {
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "notify": {
                                "type": "boolean",
                                "description": "Send notifications",
                                "default": False,
                            }
                        },
                    }
                }
            }
        }
    }
    field = _parse_request_body(op, {})[0]
    assert field.description == "Send notifications"
    assert field.default is False


def test_array_root_body_yields_no_fields_and_is_flagged() -> None:
    op = {
        "requestBody": {
            "content": {
                "application/json": {"schema": {"type": "array", "items": {"type": "string"}}}
            }
        }
    }
    assert _parse_request_body(op, {}) == []
    assert _body_root_type(op, {}) == "array"


def test_media_precedence_json_wins_over_star() -> None:
    op = {
        "requestBody": {
            "content": {
                "*/*": {
                    "schema": {"type": "object", "properties": {"fromstar": {"type": "string"}}}
                },
                "application/json": {
                    "schema": {"type": "object", "properties": {"fromjson": {"type": "string"}}}
                },
            }
        }
    }
    assert _names(_parse_request_body(op, {})) == {"fromjson"}


def test_media_precedence_merge_patch_over_star() -> None:
    op = {
        "requestBody": {
            "content": {
                "*/*": {
                    "schema": {"type": "object", "properties": {"fromstar": {"type": "string"}}}
                },
                "application/merge-patch+json": {
                    "schema": {"type": "object", "properties": {"frompatch": {"type": "string"}}}
                },
            }
        }
    }
    assert _names(_parse_request_body(op, {})) == {"frompatch"}


def test_duplicate_ref_allof_does_not_blow_up() -> None:
    """A diamond/duplicate-$ref allOf chain must expand each schema once
    (O(N)), not 2**depth. Without the shared-visited guard a 60-deep chain
    would never return."""
    depth = 60
    schemas: dict[str, Any] = {}
    for k in range(depth):
        ref = {"$ref": f"#/components/schemas/S{k + 1}"}
        schemas[f"S{k}"] = {"allOf": [ref, ref]}
    schemas[f"S{depth}"] = {"type": "object", "properties": {"leaf": {"type": "string"}}}
    op = {
        "requestBody": {
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/S0"}}}
        }
    }
    assert _names(_parse_request_body(op, schemas)) == {"leaf"}


def test_malformed_request_bodies_degrade_to_empty() -> None:
    assert _parse_request_body({}, {}) == []
    assert _parse_request_body({"requestBody": None}, {}) == []
    assert _parse_request_body({"requestBody": "nope"}, {}) == []
    assert _parse_request_body({"requestBody": {}}, {}) == []  # no content
    assert _parse_request_body({"requestBody": {"content": "x"}}, {}) == []
    assert _parse_request_body({"requestBody": {"content": {"application/json": {}}}}, {}) == []
    # schema present but not a dict
    assert (
        _parse_request_body(
            {"requestBody": {"content": {"application/json": {"schema": "bogus"}}}}, {}
        )
        == []
    )
    # dangling $ref to a missing component
    assert (
        _parse_request_body(
            {"requestBody": {"content": {"application/json": {"schema": {"$ref": "#/x/Nope"}}}}}, {}
        )
        == []
    )


# ---------------------------------------------------------------------------
# tools rendering
# ---------------------------------------------------------------------------


def _op_with_body(fields: list, *, has_body: bool = True) -> OperationSpec:
    return OperationSpec(
        operation_id="op",
        action_name="post_alerts",
        summary="Create alert",
        method="post",
        path="/alerts",
        tag="Alerts",
        has_body=has_body,
        body_description="Alert rule body",
        body_fields=fields,
    )


def test_format_body_lists_real_fields() -> None:
    from thousand_eyes_mcp.loader import ParameterSpec

    op = _op_with_body(
        [
            ParameterSpec(name="ruleName", location="body", required=True, type="string"),
            ParameterSpec(name="severity", location="body", required=False, type="integer"),
        ]
    )
    rendered = _format_body(op)
    assert "body fields (top-level):" in rendered
    assert "ruleName: string" in rendered  # required -> no '?'
    assert "severity?: integer" in rendered


def test_format_body_falls_back_when_no_fields() -> None:
    op = _op_with_body([])
    rendered = _format_body(op)
    assert "body: object" in rendered
    assert "top level" in rendered


def test_body_hint_only_present_when_group_has_body_action() -> None:
    from thousand_eyes_mcp.loader import ParameterSpec

    body_group = ToolGroup(
        name="alerts",
        display_tag="Alerts",
        operations=[
            _op_with_body(
                [ParameterSpec(name="ruleName", location="body", required=True, type="string")]
            )
        ],
    )
    ro_group = ToolGroup(
        name="tests",
        display_tag="Tests",
        operations=[
            OperationSpec(
                operation_id="g",
                action_name="get_tests",
                summary="",
                method="get",
                path="/tests",
                tag="Tests",
            )
        ],
    )
    assert _BODY_HINT in _build_description(body_group)
    assert "body fields (top-level):" in _build_description(body_group)
    assert _BODY_HINT not in _build_description(ro_group)


# ---------------------------------------------------------------------------
# End-to-end: spec on disk -> SpecLoader -> OperationSpec -> rendered description
# ---------------------------------------------------------------------------


def test_loader_to_description_end_to_end(minimal_specs_dir: Path) -> None:
    """The whole chain: the fixture's POST /alerts ($ref AlertRule with allOf,
    a readOnly _links/createdBy, and an enum $ref) must surface only the real
    writable fields, with correct types, all the way into the tool description."""
    index = SpecLoader(str(minimal_specs_dir), "7.0.88", read_write=True).load()

    post = index.by_action_name["post_alerts"]
    by_name = {f.name: f for f in post.body_fields}
    # writable fields surfaced; readOnly (createdBy, _links, alertId) dropped
    assert set(by_name) == {"ruleName", "severity", "notify"}
    assert by_name["ruleName"].required is True
    assert by_name["severity"].type == "string"  # resolved through the enum $ref
    assert by_name["notify"].type == "boolean"
    assert by_name["notify"].description == "Send notifications"

    # array-rooted PUT body is flagged, exposes no top-level fields
    put = index.by_action_name["put_alerts_labels"]
    assert put.body_array is True
    assert put.body_fields == []

    group = next(
        g for g in index.groups if any(o.action_name == "post_alerts" for o in g.operations)
    )
    desc = _build_description(group)
    assert "body fields (top-level): " in desc
    assert "ruleName: string" in desc
    assert "severity?: string" in desc
    # readOnly leaks would render as body-field signatures; assert their absence
    assert "_links?:" not in desc
    assert "createdBy" not in desc
    assert "alertId?:" not in desc
    assert "body: array" in desc  # the PUT labels action
