"""Static quality checks for MCP tool definitions."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

import jsonschema

from mcp_rig.client import ToolInfo

MIN_DESCRIPTION_WORDS = 5
SIMILARITY_THRESHOLD = 0.85
ADDITIVE_VERBS = frozenset(
    {
        "add",
        "append",
        "book",
        "charge",
        "create",
        "enqueue",
        "insert",
        "invite",
        "notify",
        "order",
        "pay",
        "post",
        "publish",
        "register",
        "send",
        "submit",
        "transfer",
    }
)
IDEMPOTENCY_PARAMETERS = frozenset(
    {
        "clientrequestid",
        "clienttoken",
        "dedupekey",
        "deduplicationid",
        "deduplicationkey",
        "dedupkey",
        "idempotencykey",
        "idempotencytoken",
    }
)


@dataclass(frozen=True)
class LintWarning:
    """One advisory tool-definition quality warning."""

    tool: str
    code: str
    message: str


def lint_tools(tools: list[ToolInfo]) -> list[LintWarning]:
    """Return deterministic quality warnings for tool definitions."""
    warnings: list[LintWarning] = []
    for tool in tools:
        warnings.extend(_lint_description(tool))
        warnings.extend(_lint_schema(tool))
        warnings.extend(_lint_retry_safety(tool))
    warnings.extend(_lint_similar(tools))
    return warnings


def _lint_description(tool: ToolInfo) -> list[LintWarning]:
    words = tool.description.split()
    if not words:
        return [LintWarning(tool.name, "no-description", "tool has no description")]
    if len(words) < MIN_DESCRIPTION_WORDS:
        return [
            LintWarning(
                tool.name,
                "short-description",
                f"description has {len(words)} words (min {MIN_DESCRIPTION_WORDS})",
            )
        ]
    return []


def _lint_schema(tool: ToolInfo) -> list[LintWarning]:
    try:
        jsonschema.Draft202012Validator.check_schema(tool.input_schema)
    except jsonschema.SchemaError as exc:
        return [LintWarning(tool.name, "invalid-schema", exc.message)]

    warnings: list[LintWarning] = []
    for name, prop in tool.input_schema.get("properties", {}).items():
        description = prop.get("description") if isinstance(prop, dict) else None
        if not isinstance(description, str) or not description.strip():
            warnings.append(
                LintWarning(
                    tool.name,
                    "param-no-description",
                    f"parameter {name!r} has no description",
                )
            )
    return warnings


def looks_side_effecting(tool: ToolInfo) -> bool:
    """Whether a tool appears to change state, judged by its annotations and name."""
    read_only = tool.annotations.get("readOnlyHint")
    if read_only is True:
        return False
    words = _name_words(tool.name)
    return read_only is False or (len(words) > 1 and words[0] in ADDITIVE_VERBS)


def _lint_retry_safety(tool: ToolInfo) -> list[LintWarning]:
    if tool.annotations.get("idempotentHint") is True or not looks_side_effecting(tool):
        return []
    properties = tool.input_schema.get("properties")
    if isinstance(properties, dict) and any(
        re.sub(r"[^a-z0-9]", "", str(name).lower()) in IDEMPOTENCY_PARAMETERS for name in properties
    ):
        return []
    return [
        LintWarning(
            tool.name,
            "retry-unsafe",
            "tool has side effects but no idempotency key or idempotentHint; "
            "a client retry after a lost response may apply it twice",
        )
    ]


def _name_words(name: str) -> list[str]:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    return [word.lower() for word in re.split(r"[^A-Za-z0-9]+", spaced) if word]


def _lint_similar(tools: list[ToolInfo]) -> list[LintWarning]:
    documented = [tool for tool in tools if tool.description.strip()]
    warnings: list[LintWarning] = []
    for index, first in enumerate(documented):
        for second in documented[index + 1 :]:
            ratio = difflib.SequenceMatcher(
                None,
                first.description.lower(),
                second.description.lower(),
            ).ratio()
            if ratio >= SIMILARITY_THRESHOLD:
                warnings.append(
                    LintWarning(
                        f"{first.name}/{second.name}",
                        "similar-tools",
                        f"descriptions are {ratio:.0%} similar; models may confuse them",
                    )
                )
    return warnings
