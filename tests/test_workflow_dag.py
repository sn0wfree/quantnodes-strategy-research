import pytest
from strategy_research.core.workflow.dag import topological_layers, validate_dag


class TestValidateDag:
    def test_empty_dag(self):
        validate_dag({})

    def test_single_node(self):
        validate_dag({"a": []})

    def test_linear_chain(self):
        validate_dag({"a": ["b"], "b": ["c"]})

    def test_diamond(self):
        validate_dag({"a": ["b", "c"], "b": ["d"], "c": ["d"]})

    def test_cycle_raises(self):
        with pytest.raises(ValueError, match="cycle"):
            validate_dag({"a": ["b"], "b": ["a"]})

    def test_self_cycle_raises(self):
        with pytest.raises(ValueError, match="cycle"):
            validate_dag({"a": ["a"]})

    def test_indirect_cycle_raises(self):
        with pytest.raises(ValueError, match="cycle"):
            validate_dag({"a": ["b"], "b": ["c"], "c": ["a"]})

    def test_disconnected_components(self):
        validate_dag({"a": ["b"], "c": ["d"]})


class TestTopologicalLayers:
    def test_empty(self):
        assert topological_layers({}) == []

    def test_single_node(self):
        result = topological_layers({"a": []})
        assert result == [["a"]]

    def test_linear_chain(self):
        result = topological_layers({"a": ["b"], "b": ["c"]})
        assert result == [["a"], ["b"], ["c"]]

    def test_diamond(self):
        result = topological_layers({"a": ["b", "c"], "b": ["d"], "c": ["d"]})
        assert len(result) == 3
        assert result[0] == ["a"]
        assert sorted(result[1]) == ["b", "c"]
        assert result[2] == ["d"]

    def test_parallel_agents_same_layer(self):
        result = topological_layers({"a": ["c"], "b": ["c"]})
        assert len(result) == 2
        assert sorted(result[0]) == ["a", "b"]
        assert result[1] == ["c"]

    def test_complex_dag(self):
        adj = {
            "researcher": ["data_quality", "factor_analyst"],
            "data_quality": ["strategist"],
            "factor_analyst": ["strategist"],
            "strategist": ["portfolio_construction"],
        }
        result = topological_layers(adj)
        assert len(result) == 4
        assert result[0] == ["researcher"]
        assert sorted(result[1]) == ["data_quality", "factor_analyst"]
        assert result[2] == ["strategist"]
        assert result[3] == ["portfolio_construction"]

    def test_result_is_sorted(self):
        result = topological_layers({"z": ["a"], "a": ["m"], "m": ["b"]})
        for layer in result:
            assert layer == sorted(layer)
