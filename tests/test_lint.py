import pytest

from mcp_rig.client import ToolInfo, connect
from mcp_rig.lint import lint_tools

GOOD_SCHEMA = {
    "type": "object",
    "properties": {
        "city": {
            "type": "string",
            "description": "City name to look up",
        }
    },
}


def tool(
    name: str,
    description: str = "Return the current weather for a city.",
    schema: dict | None = None,
    annotations: dict | None = None,
) -> ToolInfo:
    return ToolInfo(name, description, GOOD_SCHEMA if schema is None else schema, annotations or {})


def codes(warnings):
    return [(warning.tool, warning.code) for warning in warnings]


def test_clean_tool_has_no_warnings():
    assert lint_tools([tool("weather")]) == []


def test_empty_and_whitespace_descriptions_are_missing():
    warnings = lint_tools(
        [
            tool("empty", description=""),
            tool("whitespace", description=" \t\n"),
        ]
    )

    assert codes(warnings) == [
        ("empty", "no-description"),
        ("whitespace", "no-description"),
    ]


def test_four_word_description_is_too_short():
    warnings = lint_tools([tool("brief", description="Return weather for city")])

    assert codes(warnings) == [("brief", "short-description")]
    assert warnings[0].message == "description has 4 words (min 5)"


def test_invalid_schema_produces_one_warning():
    warnings = lint_tools([tool("weather", schema={"type": "not-a-type"})])

    assert codes(warnings) == [("weather", "invalid-schema")]


def test_missing_and_whitespace_parameter_descriptions_are_reported():
    schema = {
        "type": "object",
        "properties": {
            "city": {"type": "string"},
            "country": {"type": "string", "description": "  "},
        },
    }

    warnings = lint_tools([tool("weather", schema=schema)])

    assert codes(warnings) == [
        ("weather", "param-no-description"),
        ("weather", "param-no-description"),
    ]
    assert [warning.message for warning in warnings] == [
        "parameter 'city' has no description",
        "parameter 'country' has no description",
    ]


def test_non_mapping_properties_is_invalid_without_raising():
    warnings = lint_tools(
        [tool("weather", schema={"type": "object", "properties": []})]
    )

    assert codes(warnings) == [("weather", "invalid-schema")]


def test_described_parameters_have_no_schema_warnings():
    assert lint_tools([tool("weather", schema=GOOD_SCHEMA)]) == []


@pytest.mark.parametrize("subschema", [True, False])
def test_boolean_property_schema_is_valid_and_reported_as_undocumented(subschema):
    schema = {
        "type": "object",
        "properties": {"value": subschema},
    }

    warnings = lint_tools([tool("boolean_property", schema=schema)])

    assert codes(warnings) == [("boolean_property", "param-no-description")]
    assert warnings[0].message == "parameter 'value' has no description"


def test_similar_descriptions_are_reported_once():
    warnings = lint_tools(
        [
            tool("first", description="Read a text file from disk"),
            tool("second", description="Read a data file from disk"),
        ]
    )

    assert codes(warnings) == [("first/second", "similar-tools")]
    assert warnings[0].message == "descriptions are 88% similar; models may confuse them"


def test_descriptions_below_similarity_threshold_are_not_reported():
    warnings = lint_tools(
        [
            tool("first", description="Read a text file from disk"),
            tool("second", description="Read a text path from disk"),
        ]
    )

    assert warnings == []


def test_undocumented_tools_do_not_participate_in_similarity_checks():
    warnings = lint_tools(
        [
            tool("first", description=""),
            tool("second", description="Read a text file from disk"),
        ]
    )

    assert codes(warnings) == [("first", "no-description")]


def test_each_unordered_similarity_pair_is_reported_once():
    description = "Read a text file from disk"
    warnings = lint_tools(
        [
            tool("first", description=description),
            tool("second", description=description),
            tool("third", description=description),
        ]
    )

    assert codes(warnings) == [
        ("first/second", "similar-tools"),
        ("first/third", "similar-tools"),
        ("second/third", "similar-tools"),
    ]


def test_warning_order_is_per_tool_then_similarity():
    missing_description_schema = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
    }
    warnings = lint_tools(
        [
            tool(
                "first",
                description="Read a text file from disk",
                schema=missing_description_schema,
            ),
            tool(
                "second",
                description="Read a data file from disk",
                schema={"type": "not-a-type"},
            ),
        ]
    )

    assert codes(warnings) == [
        ("first", "param-no-description"),
        ("second", "invalid-schema"),
        ("first/second", "similar-tools"),
    ]


@pytest.mark.anyio
async def test_fixture_server_exposes_expected_lint_warnings(fixture_spec):
    async with connect(fixture_spec) as probe:
        warnings = lint_tools(await probe.list_tools())

    found = set(codes(warnings))
    assert ("undocumented", "no-description") in found
    assert ("add", "param-no-description") in found


@pytest.mark.parametrize("name", ["create_record", "sendEmail", "submit-order", "charge.card", "add_item"])
def test_additive_tool_without_idempotency_is_retry_unsafe(name):
    warnings = lint_tools([tool(name)])

    assert codes(warnings) == [(name, "retry-unsafe")]
    assert "may apply it twice" in warnings[0].message


@pytest.mark.parametrize("name", ["add", "create", "get_user", "delete_record", "update_record", "addressbook"])
def test_reads_bare_verbs_and_naturally_idempotent_verbs_are_not_flagged(name):
    assert lint_tools([tool(name)]) == []


@pytest.mark.parametrize(
    "parameter",
    ["idempotency_key", "idempotencyKey", "dedupe_key", "client_request_id", "clientToken", "deduplication-id"],
)
def test_idempotency_key_parameter_makes_tool_retry_safe(parameter):
    schema = {"type": "object", "properties": {parameter: {"type": "string", "description": "Key for retries"}}}

    assert lint_tools([tool("create_record", schema=schema)]) == []


@pytest.mark.parametrize("annotations", [{"idempotentHint": True}, {"readOnlyHint": True}])
def test_idempotent_or_read_only_hints_make_tool_retry_safe(annotations):
    assert lint_tools([tool("create_record", annotations=annotations)]) == []


def test_explicit_side_effect_hint_is_checked_regardless_of_name():
    warnings = lint_tools([tool("ledger", annotations={"readOnlyHint": False, "idempotentHint": False})])

    assert codes(warnings) == [("ledger", "retry-unsafe")]
