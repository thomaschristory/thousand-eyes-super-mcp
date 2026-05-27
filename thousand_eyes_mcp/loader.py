"""
loader.py — loads the Cisco ThousandEyes OpenAPI spec for a given version,
derives stable per-operation action names, splits operations into tools
using an adaptive size-driven algorithm, and builds a flat index keyed by
action name for O(1) dispatch.

The ThousandEyes spec ships as a single ``api.yaml`` file but the loader
glob-merges every ``*.yaml`` / ``*.yml`` / ``*.json`` under
``specs/<version>/`` so future split-spec releases work without code change.

Splitting algorithm (mirrors the sibling super-MCP projects):
  Given max_actions_per_tool N (0 disables splitting):

  1. Group ops by section = first component of the tag (before " - ").
  2. For each section:
       - if len <= N (or N == 0): emit one tool named after the section.
       - else split by sub-tag (second component of the tag).
           - for each sub-tag, if len <= N: emit one tool <section>_<subtag>.
           - else recurse by URL path segments, starting at depth 3, deepening
             one segment at a time until every bucket <= N OR depth 5 is
             reached. Emit one tool per leaf bucket.
       - sibling buckets with <4 ops collapse into <parent>_misc.
       - any tool still over N at max depth logs a WARNING; oversized
         tool is emitted anyway.

Action names are derived from (verb, tag-component, last-non-templated
path segment) and deduplicated within a tool. They are stable across
upstream ``operationId`` churn between releases.

All log lines route to stderr — stdout is reserved for the MCP JSON-RPC
stream on stdio transport.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RO_METHODS = frozenset({"get"})
RW_METHODS = frozenset({"get", "post", "put", "delete", "patch"})

SKIP_METHODS = frozenset({"head", "options", "trace"})

DEFAULT_MAX_ACTIONS_PER_TOOL = 80
PATH_SPLIT_START_DEPTH = 3
PATH_SPLIT_MAX_DEPTH = 5
MISC_BUCKET_THRESHOLD = 4  # sub-tags / path buckets with fewer ops collapse to <parent>_misc


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ParameterSpec:
    name: str
    location: str  # "path" | "query" | "header" | "cookie"
    required: bool = False
    type: str = "string"
    description: str = ""
    default: object = None


@dataclass
class OperationSpec:
    operation_id: str  # upstream operationId — kept as back-reference for --diff
    action_name: str  # stable, derived name used by the dispatcher and MCP tools
    summary: str
    method: str  # lowercase: get | post | put | delete | patch
    path: str
    tag: str
    parameters: list[ParameterSpec] = field(default_factory=list)
    has_body: bool = False
    body_description: str = ""
    pagination: Literal["link", "cursor", "offset"] | None = None


@dataclass
class ToolGroup:
    """A bucket of operations exposed as one MCP tool."""

    name: str  # snake_case tool name
    display_tag: str  # human-readable header, e.g. "Tests / BGP Tests"
    operations: list[OperationSpec] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return self.name

    @property
    def tag(self) -> str:
        return self.display_tag


@dataclass
class SpecIndex:
    """Flat lookup: action_name -> OperationSpec, built for O(1) dispatch."""

    by_action_name: dict[str, OperationSpec] = field(default_factory=dict)
    by_operation_id: dict[str, OperationSpec] = field(default_factory=dict)
    groups: list[ToolGroup] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Slug / segment helpers
# ---------------------------------------------------------------------------

_SLUG_REPLACE = str.maketrans(
    {
        " ": "_",
        "-": "_",
        "/": "_",
        ".": "_",
        "(": "",
        ")": "",
        ",": "",
        "[": "",
        "]": "",
    }
)


def _slugify(text: str) -> str:
    """Lowercase, replace separators with underscore, collapse repeats."""
    if not text:
        return ""
    out = text.lower().translate(_SLUG_REPLACE)
    return re.sub(r"_+", "_", out).strip("_")


_TEMPLATED_RE = re.compile(r"^\{[^}]+\}$")


def _path_segments(path: str) -> list[str]:
    """Split a URL path into segments."""
    return [s for s in path.split("/") if s]


def _structural_segments(path: str) -> list[str]:
    """
    Path segments with templated placeholders ({foo}) dropped.

    Used for bucketing in _split_by_path so deepening picks up the next
    *concrete* URL segment rather than getting stuck on a {testId}
    placeholder that's identical across every operation in a sub-tag.
    """
    return [s for s in _path_segments(path) if not _TEMPLATED_RE.match(s)]


def _last_non_templated_segment(segments: list[str]) -> str:
    for seg in reversed(segments):
        if not _TEMPLATED_RE.match(seg):
            return seg
    return "root"


# ThousandEyes mounts everything under ``/v7/`` already in the servers URL,
# but a few operations use absolute paths that re-include it. Strip both
# forms so the disambiguator doesn't carry a meaningless ``v7`` segment.
_API_PREFIXES = ("/v7/",)


def _path_discriminator(path: str, exclude_leaf: str, *, strip_api_prefix: bool = True) -> str:
    """Build a slug from a URL path for use as a disambiguating suffix.

    By default strips the API prefix, removes path params, drops the trailing
    leaf already encoded in the bare action name, then slugifies. Pass
    ``strip_api_prefix=False`` to keep the prefix as part of the discriminator
    — useful when two paths that differ only in their API family collide
    otherwise.
    """
    stripped = path
    if strip_api_prefix:
        for prefix in _API_PREFIXES:
            if stripped.startswith(prefix):
                stripped = stripped[len(prefix) :]
                break
    stripped = stripped.strip("/")
    segments = [s for s in stripped.split("/") if s and not _TEMPLATED_RE.match(s)]
    if segments and _slugify(segments[-1]) == exclude_leaf:
        segments = segments[:-1]
    return _slugify("_".join(segments)) or "root"


def _tag_section(tag: str) -> str:
    return tag.split(" - ", 1)[0].strip()


def _tag_subtag(tag: str) -> str:
    """Second component if present, otherwise first."""
    parts = tag.split(" - ", 1)
    return (parts[1] if len(parts) == 2 else parts[0]).strip()


# ---------------------------------------------------------------------------
# Action-name derivation
# ---------------------------------------------------------------------------


def _derive_action_name(method: str, path: str, tag: str) -> str:
    """
    Build a stable per-operation action name from (verb, tag-component, last URL segment).

    Independent of upstream ``operationId``, which can churn between releases.
    """
    verb = method.lower()
    tag_slug = _slugify(_tag_subtag(tag))
    last_seg = _slugify(_last_non_templated_segment(_path_segments(path)))

    parts = [verb]
    if tag_slug:
        parts.append(tag_slug)
    if last_seg and last_seg != tag_slug:
        parts.append(last_seg)
    return "_".join(parts)


# ---------------------------------------------------------------------------
# OpenAPI parameter parsing
# ---------------------------------------------------------------------------


def _extract_type(schema: dict[str, Any]) -> str:
    if not schema:
        return "string"
    if "$ref" in schema:
        return "object"
    t = schema.get("type", "string")
    return t if isinstance(t, str) else "string"


def _parse_parameters(raw_params: list[dict[str, Any]]) -> list[ParameterSpec]:
    result: list[ParameterSpec] = []
    for p in raw_params or []:
        schema = p.get("schema", {})
        result.append(
            ParameterSpec(
                name=p.get("name", ""),
                location=p.get("in", "query"),
                required=p.get("required", False),
                type=_extract_type(schema),
                description=p.get("description", ""),
                default=schema.get("default"),
            )
        )
    return result


_OFFSET_SIZE_PARAMS = {"limit", "count", "pageSize", "max"}
_OFFSET_INDEX_PARAMS = {"offset", "startIndex"}


def _detect_pagination_style(
    parameters: list[ParameterSpec],
) -> Literal["link", "cursor", "offset"] | None:
    """Decide pagination style from a parsed parameter list.

    ThousandEyes pagination patterns:
    - "cursor": ``cursor`` query param — server returns ``_links.next.href``
      with a cursor-bearing URL. Predominant in v7 (Tests Results, Alerts).
    - "offset": ``offset`` + ``limit``/``max``/``pageSize``. Older endpoints.

    The dispatcher's CursorPaginator follows ``_links.next.href`` directly,
    so any cursor-style operation is auto-followable.
    """
    names = {p.name for p in parameters if p.location == "query"}
    if "cursor" in names:
        return "cursor"
    if (names & _OFFSET_INDEX_PARAMS) and (names & _OFFSET_SIZE_PARAMS):
        return "offset"
    return None


def _parse_operation(
    path: str,
    method: str,
    operation: dict[str, Any],
    tag: str,
) -> OperationSpec:
    has_body = "requestBody" in operation
    body_desc = ""
    if has_body:
        body_desc = operation["requestBody"].get("description", "Request body (JSON)")

    op_id = operation.get("operationId", f"{method}_{path}")
    parameters = _parse_parameters(operation.get("parameters", []))
    return OperationSpec(
        operation_id=op_id,
        action_name=_derive_action_name(method, path, tag),
        summary=operation.get("summary") or operation.get("description", ""),
        method=method,
        path=path,
        tag=tag,
        parameters=parameters,
        has_body=has_body,
        body_description=body_desc,
        pagination=_detect_pagination_style(parameters),
    )


# ---------------------------------------------------------------------------
# Adaptive splitter
# ---------------------------------------------------------------------------


def _split_section(
    section: str,
    section_ops: list[OperationSpec],
    threshold: int,
) -> list[ToolGroup]:
    """Split a section's operations into ToolGroups per the adaptive algorithm."""
    section_slug = _slugify(section)
    if threshold <= 0 or len(section_ops) <= threshold:
        return [ToolGroup(name=section_slug, display_tag=section, operations=list(section_ops))]

    # Sub-tag split
    by_subtag: dict[str, list[OperationSpec]] = {}
    for op in section_ops:
        by_subtag.setdefault(_tag_subtag(op.tag), []).append(op)

    leaf_groups: list[ToolGroup] = []
    misc_ops: list[OperationSpec] = []

    for subtag, ops in by_subtag.items():
        subtag_slug = _slugify(subtag)
        if len(ops) < MISC_BUCKET_THRESHOLD:
            misc_ops.extend(ops)
            continue
        if len(ops) <= threshold:
            name = f"{section_slug}_{subtag_slug}" if subtag_slug != section_slug else section_slug
            display = f"{section} / {subtag}" if subtag != section else section
            leaf_groups.append(ToolGroup(name=name, display_tag=display, operations=ops))
            continue
        # Sub-tag still too large — recurse on URL path segments.
        leaf_groups.extend(
            _split_by_path(
                section=section,
                section_slug=section_slug,
                subtag=subtag,
                subtag_slug=subtag_slug,
                ops=ops,
                threshold=threshold,
            )
        )

    if misc_ops:
        leaf_groups.append(
            ToolGroup(
                name=f"{section_slug}_misc",
                display_tag=f"{section} / misc",
                operations=misc_ops,
            )
        )

    return leaf_groups


def _common_prefix_segments(keys: list[str]) -> int:
    """Count the leading '/'-separated segments shared by every key."""
    if not keys:
        return 0
    seg_lists = [k.split("/") for k in keys]
    shortest = min(len(s) for s in seg_lists)
    for i in range(shortest):
        if any(s[i] != seg_lists[0][i] for s in seg_lists):
            return i
    return shortest


def _bucket_by_depth(ops: list[OperationSpec], depth: int) -> dict[str, list[OperationSpec]]:
    """
    Bucket ops by their first `depth` structural (non-templated) path segments.

    Templated segments are skipped so deepening picks up the next concrete
    URL component instead of stalling on a placeholder.
    """
    buckets: dict[str, list[OperationSpec]] = {}
    for op in ops:
        segs = _structural_segments(op.path)
        key = "/".join(segs[:depth]) if segs else "/"
        buckets.setdefault(key, []).append(op)
    return buckets


def _split_by_path(
    section: str,
    section_slug: str,
    subtag: str,
    subtag_slug: str,
    ops: list[OperationSpec],
    threshold: int,
) -> list[ToolGroup]:
    """
    Bucket `ops` by the first N segments of their URL path, deepening N
    until every bucket is <= threshold or PATH_SPLIT_MAX_DEPTH is reached.
    Sibling buckets with <MISC_BUCKET_THRESHOLD ops collapse to <parent>_misc.
    Buckets still over threshold at max depth log a warning and are still emitted.
    """
    final_buckets = _bucket_by_depth(ops, PATH_SPLIT_START_DEPTH)
    chosen_depth = PATH_SPLIT_START_DEPTH
    last_tried_depth = PATH_SPLIT_START_DEPTH

    for depth in range(PATH_SPLIT_START_DEPTH + 1, PATH_SPLIT_MAX_DEPTH + 1):
        if all(len(b) <= threshold for b in final_buckets.values()):
            break
        last_tried_depth = depth
        deeper = _bucket_by_depth(ops, depth)
        if len(deeper) > len(final_buckets):
            final_buckets = deeper
            chosen_depth = depth

    parent_slug = f"{section_slug}_{subtag_slug}" if subtag_slug != section_slug else section_slug
    parent_display = f"{section} / {subtag}" if subtag != section else section

    if len(final_buckets) == 1:
        only_ops = next(iter(final_buckets.values()))
        if len(only_ops) > threshold:
            print(
                f"[loader] WARNING: tool '{parent_slug}' has {len(only_ops)} actions "
                f"(threshold={threshold}) — path splitting tried depths "
                f"{PATH_SPLIT_START_DEPTH}-{last_tried_depth} (max {PATH_SPLIT_MAX_DEPTH}) "
                f"and could not subdivide further.",
                file=sys.stderr,
            )
        return [ToolGroup(name=parent_slug, display_tag=parent_display, operations=only_ops)]

    common = _common_prefix_segments(list(final_buckets.keys()))

    leaves: list[ToolGroup] = []
    misc_ops: list[OperationSpec] = []

    for key, bucket_ops in final_buckets.items():
        if len(bucket_ops) < MISC_BUCKET_THRESHOLD:
            misc_ops.extend(bucket_ops)
            continue
        key_segs = key.split("/")
        discriminator = key_segs[common:] or [key_segs[-1] if key_segs else "root"]
        disc_slug = _slugify("_".join(discriminator)) or "root"
        name = f"{parent_slug}_{disc_slug}"
        display = f"{section} / {subtag} / {'/'.join(discriminator)}"
        leaves.append(ToolGroup(name=name, display_tag=display, operations=bucket_ops))
        if len(bucket_ops) > threshold:
            print(
                f"[loader] WARNING: tool '{name}' has {len(bucket_ops)} actions "
                f"(threshold={threshold}) — hit PATH_SPLIT_MAX_DEPTH={PATH_SPLIT_MAX_DEPTH} "
                f"at depth {chosen_depth} without further splitting.",
                file=sys.stderr,
            )

    if misc_ops:
        leaves.append(
            ToolGroup(
                name=f"{parent_slug}_misc",
                display_tag=f"{section} / {subtag} / misc",
                operations=misc_ops,
            )
        )

    return leaves


# ---------------------------------------------------------------------------
# Action-name deduplication
# ---------------------------------------------------------------------------


def _dedupe_tool_names(groups: list[ToolGroup]) -> None:
    """
    In-place: ensure every group.name is unique. If two groups slug to the
    same tool name (different sub-tags producing identical slugs), the
    second and subsequent ones get _2, _3, ... appended.
    """
    seen: dict[str, int] = {}
    for group in groups:
        base = group.name
        count = seen.get(base, 0)
        if count > 0:
            group.name = f"{base}_{count + 1}"
        seen[base] = count + 1


def _disambiguate_cross_tool(groups: list[ToolGroup]) -> None:
    """Pre-pass: any action_name appearing in >1 op (across all tools) is
    rewritten as ``<bare>__<path-discriminator>`` for *every* colliding op.

    Backward-compat invariant: an action_name that was unique stays unchanged.

    Two-pass discriminator: pass 1 strips the API prefix; any
    ``<bare>__<disc>`` slugs that *still* collide after pass 1 are re-resolved
    with the full path (no prefix stripped) so the family becomes part of the
    discriminator. Residual collisions get a numeric suffix.
    """
    occurrences: dict[str, list[OperationSpec]] = {}
    for group in groups:
        for op in group.operations:
            occurrences.setdefault(op.action_name, []).append(op)

    for bare, ops in occurrences.items():
        if len(ops) < 2:
            continue
        leaf = bare.rsplit("_", 1)[-1]
        for op in ops:
            disc = _path_discriminator(op.path, exclude_leaf=leaf)
            op.action_name = f"{bare}__{disc}"

    occurrences2: dict[str, list[OperationSpec]] = {}
    for group in groups:
        for op in group.operations:
            occurrences2.setdefault(op.action_name, []).append(op)

    for name, ops in occurrences2.items():
        if len(ops) < 2:
            continue
        bare = name.rsplit("__", 1)[0] if "__" in name else name
        leaf = bare.rsplit("_", 1)[-1]
        ops_sorted = sorted(ops, key=lambda o: (o.method, o.path))
        rewritten: dict[str, int] = {}
        for op in ops_sorted:
            disc = _path_discriminator(op.path, exclude_leaf=leaf, strip_api_prefix=False)
            candidate = f"{bare}__{disc}"
            count = rewritten.get(candidate, 0)
            op.action_name = candidate if count == 0 else f"{candidate}__{count + 1}"
            rewritten[candidate] = count + 1


def _dedupe_action_names(group: ToolGroup) -> None:
    """In-place: make every op.action_name unique within its tool by appending _2, _3, ..."""
    seen: dict[str, int] = {}
    for op in group.operations:
        base = op.action_name
        count = seen.get(base, 0)
        if count > 0:
            op.action_name = f"{base}_{count + 1}"
        seen[base] = count + 1


# ---------------------------------------------------------------------------
# Main loader
# ---------------------------------------------------------------------------


class SpecLoader:
    def __init__(
        self,
        specs_dir: str,
        version: str,
        read_write: bool = False,
        max_actions_per_tool: int = DEFAULT_MAX_ACTIONS_PER_TOOL,
    ):
        self.version_dir = Path(specs_dir) / version
        self.version = version
        self.allowed_methods = RW_METHODS if read_write else RO_METHODS
        self.max_actions_per_tool = max(0, int(max_actions_per_tool))

        if not self.version_dir.exists():
            raise FileNotFoundError(
                f"Spec directory not found: {self.version_dir}\n"
                f"Download the OpenAPI spec from Cisco DevNet and place it in "
                f"{self.version_dir}/, or set auto_fetch: true in thousand-eyes-mcp.yaml."
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> SpecIndex:
        merged = self._load_and_merge()
        ops = self._extract_operations(merged)
        ops = [op for op in ops if op.method in self.allowed_methods]
        groups = self._split_into_groups(ops)
        return self._build_index(groups)

    # ------------------------------------------------------------------
    # Step 1: load all sub-spec files and merge into one dict
    # ------------------------------------------------------------------

    def _load_and_merge(self) -> dict[str, Any]:
        spec_files = sorted(
            list(self.version_dir.glob("*.yaml"))
            + list(self.version_dir.glob("*.yml"))
            + list(self.version_dir.glob("*.json"))
        )
        if not spec_files:
            raise FileNotFoundError(
                f"No spec files (*.yaml | *.yml | *.json) found in {self.version_dir}"
            )

        merged: dict[str, Any] = {
            "paths": {},
            "components": {"schemas": {}},
        }

        loader_cls: type[yaml.SafeLoader] = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

        for spec_file in spec_files:
            print(f"[loader] Loading {spec_file.name}", file=sys.stderr)
            try:
                if spec_file.suffix == ".json":
                    import json

                    spec = json.loads(spec_file.read_text())
                else:
                    spec = yaml.load(spec_file.read_text(), Loader=loader_cls)
            except (yaml.YAMLError, ValueError) as e:
                print(f"[loader] WARNING: Failed to parse {spec_file.name}: {e}", file=sys.stderr)
                continue

            merged["paths"].update(spec.get("paths", {}))
            merged["components"]["schemas"].update(spec.get("components", {}).get("schemas", {}))

        print(
            f"[loader] Loaded {len(spec_files)} spec file(s), {len(merged['paths'])} total paths",
            file=sys.stderr,
        )
        return merged

    # ------------------------------------------------------------------
    # Step 2: flatten paths/methods into OperationSpec
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_operations(spec: dict[str, Any]) -> list[OperationSpec]:
        ops: list[OperationSpec] = []
        for path, path_item in spec.get("paths", {}).items():
            for method, operation in path_item.items():
                if method.lower() in SKIP_METHODS:
                    continue
                if not isinstance(operation, dict):
                    continue
                tags = operation.get("tags") or ["Untagged"]
                ops.append(_parse_operation(path, method.lower(), operation, tags[0]))
        return ops

    # ------------------------------------------------------------------
    # Step 3: adaptive split into ToolGroups
    # ------------------------------------------------------------------

    def _split_into_groups(self, ops: list[OperationSpec]) -> list[ToolGroup]:
        by_section: dict[str, list[OperationSpec]] = {}
        for op in ops:
            by_section.setdefault(_tag_section(op.tag), []).append(op)

        groups: list[ToolGroup] = []
        for section, section_ops in by_section.items():
            groups.extend(_split_section(section, section_ops, self.max_actions_per_tool))

        _dedupe_tool_names(groups)
        _disambiguate_cross_tool(groups)
        for group in groups:
            _dedupe_action_names(group)

        threshold = self.max_actions_per_tool
        if threshold > 0:
            for group in groups:
                if group.name.endswith("_misc") and len(group.operations) > threshold:
                    print(
                        f"[loader] WARNING: misc tool '{group.name}' has "
                        f"{len(group.operations)} actions (threshold={threshold}) — "
                        f"many small sibling sub-tags collapsed past the cap.",
                        file=sys.stderr,
                    )

        mode = "RW" if self.allowed_methods == RW_METHODS else "RO"
        print(
            f"[loader] Mode={mode}, max_actions_per_tool={threshold} -> "
            f"{len(groups)} tool(s), {sum(len(g.operations) for g in groups)} operations",
            file=sys.stderr,
        )
        return groups

    # ------------------------------------------------------------------
    # Step 4: build flat indexes for the dispatcher and the diff utility
    # ------------------------------------------------------------------

    @staticmethod
    def _build_index(groups: list[ToolGroup]) -> SpecIndex:
        index = SpecIndex(groups=groups)
        for group in groups:
            for op in group.operations:
                if op.action_name in index.by_action_name:
                    print(
                        f"[loader] WARNING: duplicate action_name '{op.action_name}' "
                        f"after dedup — keeping first occurrence",
                        file=sys.stderr,
                    )
                else:
                    index.by_action_name[op.action_name] = op
                # operation_id duplicates can happen across tools — keep the first.
                index.by_operation_id.setdefault(op.operation_id, op)

        print(
            f"[loader] Index built: {len(index.by_action_name)} actions across {len(groups)} tools",
            file=sys.stderr,
        )
        return index


# Back-compat alias.
TagGroup = ToolGroup
