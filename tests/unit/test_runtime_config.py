# Contract for the runtime configuration and the output-gate cleared set.
#
# Two things are pinned here that no other test can see:
#
#   * the SHIPPED config/config.yaml is loadable and every key it declares is one
#     the graph actually validates — a declaration nothing reads is how a repo
#     ends up shipping settings that do nothing;
#   * the set of state fields the output gate clears covers every field that can
#     carry document text, so a new output-bearing field cannot quietly join the
#     state without joining the cleared set.

from pathlib import Path

import pytest

from src.graph.graph import _GOVERNANCE_BOUNDS, declared_governance, runtime_config

_REPO_ROOT = Path(__file__).resolve().parents[2]


class TestShippedConfig:
    def test_the_shipped_file_loads(self):
        config = runtime_config()
        assert config, "config/config.yaml must be present and parseable"

    def test_every_declared_governance_key_is_validated(self):
        """A key in the file that the graph does not bounds-check is a dead
        declaration: it reaches no node and no reader can tell."""
        declared = set(runtime_config().get("governance", {}))
        assert declared <= set(_GOVERNANCE_BOUNDS), (
            f"undeclared governance keys in config/config.yaml: " f"{declared - set(_GOVERNANCE_BOUNDS)}"
        )

    def test_the_shipped_values_are_the_ones_that_take_effect(self):
        """Every shipped value must be inside its bounds — otherwise the file
        says one thing and the agent silently does another."""
        config = runtime_config()
        effective = declared_governance(config)
        for key, value in config.get("governance", {}).items():
            assert effective[key] == value, (
                f"{key}={value!r} in config/config.yaml is out of bounds and is "
                f"silently replaced by {effective[key]!r}"
            )

    def test_the_output_gate_invariant_is_declared_and_true(self):
        """`security.s3_gate_enabled` is an assertion, not a switch.

        The gate in src/nodes/post_process_node.py runs unconditionally and reads
        nothing from this file — a security control that configuration can turn
        off is not a control. This asserts the declaration keeps stating the truth.
        """
        assert runtime_config()["security"]["s3_gate_enabled"] is True

    def test_no_code_reads_the_gate_flag_as_a_switch(self):
        for path in (_REPO_ROOT / "src").rglob("*.py"):
            assert "s3_gate_enabled" not in path.read_text(
                encoding="utf-8"
            ), f"{path.name} reads the gate flag; the gate must not be configurable off"


class TestGovernanceBounds:
    def test_defaults_apply_when_nothing_is_declared(self):
        settings = declared_governance({})
        for key, (_, _, default) in _GOVERNANCE_BOUNDS.items():
            assert settings[key] == default

    def test_none_config_is_handled(self):
        assert declared_governance(None) == declared_governance({})

    @pytest.mark.parametrize("key", list(_GOVERNANCE_BOUNDS))
    def test_a_value_below_the_bound_falls_back(self, key):
        low, _, default = _GOVERNANCE_BOUNDS[key]
        assert declared_governance({"governance": {key: low - 1}})[key] == default

    @pytest.mark.parametrize("key", list(_GOVERNANCE_BOUNDS))
    def test_a_value_above_the_bound_falls_back(self, key):
        _, high, default = _GOVERNANCE_BOUNDS[key]
        assert declared_governance({"governance": {key: high + 1}})[key] == default

    @pytest.mark.parametrize("key", list(_GOVERNANCE_BOUNDS))
    def test_an_in_range_value_is_honoured(self, key):
        low, high, _ = _GOVERNANCE_BOUNDS[key]
        chosen = (low + high) // 2
        assert declared_governance({"governance": {key: chosen}})[key] == chosen

    @pytest.mark.parametrize("bad", [True, False, "25", 2.5, None, [], {}])
    def test_a_non_integer_falls_back(self, bad):
        """Booleans included — isinstance(True, int) is True, so `max_field_chars:
        true` would otherwise truncate every field to a single character."""
        settings = declared_governance({"governance": {"max_field_chars": bad}})
        assert settings["max_field_chars"] == _GOVERNANCE_BOUNDS["max_field_chars"][2]

    def test_a_non_mapping_governance_block_falls_back(self):
        assert declared_governance({"governance": "nope"}) == declared_governance({})


class TestClearedSetInventory:
    """The output gate clears every field that can carry document text.

    Inert provenance fields (a boolean flag, a list of field NAMES) may stay in
    state, but only deliberately: this test enumerates the State schema and fails
    when a field appears that is in neither list, so a future field cannot join
    the state and skip the cleared set unnoticed.
    """

    # Fields that carry no document text and are safe to leave in state.
    _INERT_STATE_FIELDS = {
        "validated_input",  # the caller's own input, not generated content
        "enriched_context",  # channel metadata; the channel is an inert id
        "governance_settings",  # validated integers from config/config.yaml
        "model_metadata",  # bounded caller declarations, never released
        "human_review_required",  # boolean
        "redacted_fields",  # list of declared FIELD NAMES, not values
        "result",  # cleared explicitly by the gate, asserted below
        "trace_id",
        "correlation_id",
    }

    def test_every_state_field_is_classified(self):
        from framework.schemas.agent_state import AgentState
        from src.nodes.post_process_node import _OUTPUT_BEARING_FIELDS
        from src.schemas.state import State

        own_fields = set(State.__annotations__) - set(AgentState.__annotations__)
        unclassified = own_fields - set(_OUTPUT_BEARING_FIELDS) - self._INERT_STATE_FIELDS
        assert not unclassified, (
            f"new State fields are neither cleared by the output gate nor declared " f"inert: {sorted(unclassified)}"
        )

    def test_the_gate_clears_result_explicitly(self):
        """`result` is not in _OUTPUT_BEARING_FIELDS because the gate writes it by
        name on every path; this pins that it is never merely omitted."""
        from src.nodes.post_process_node import PostProcessNode

        result = PostProcessNode().execute(
            {
                "governance_document": "This system is fully compliant.",
                "human_review_required": False,
            }
        )
        assert "result" in result and result["result"] is None
