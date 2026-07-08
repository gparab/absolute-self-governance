import pytest
from self_governance.dimensioning import dimension_swarm, LazyList
from self_governance.models import SwarmConfig, Agent

def test_dimensioning_basic():
    requirement_vector = [2.0, 3.0]
    transition_matrix = [[1.0, 0.0], [0.0, 1.0]]
    
    config = dimension_swarm(requirement_vector, transition_matrix)
    assert isinstance(config, SwarmConfig)
    
    swarm = config.swarm
    assert isinstance(swarm, LazyList)
    assert isinstance(swarm, list)
    assert len(swarm) == 5

def test_lazy_list_indexing():
    prefix_sums = [2, 5]
    total_count = 5
    lazy_list = LazyList(prefix_sums, total_count)
    
    # Retrieve elements
    agent_0 = lazy_list[0]
    assert isinstance(agent_0, Agent)
    assert agent_0.role == "role_0"
    
    agent_2 = lazy_list[2]
    assert agent_2.role == "role_1"
    
    # Negative index
    agent_last = lazy_list[-1]
    assert agent_last.role == "role_1"
    
    # Index out of bounds
    with pytest.raises(IndexError):
        _ = lazy_list[5]
    with pytest.raises(IndexError):
        _ = lazy_list[-6]
        
    # Non-integer index
    with pytest.raises(TypeError):
        _ = lazy_list["first"]

def test_lazy_list_slicing_and_iteration():
    prefix_sums = [2, 5]
    total_count = 5
    lazy_list = LazyList(prefix_sums, total_count)
    
    # Slicing
    slice_res = lazy_list[1:3]
    assert isinstance(slice_res, list)
    assert len(slice_res) == 2
    assert slice_res[0].role == "role_0"
    assert slice_res[1].role == "role_1"
    
    # Iteration
    agents = list(lazy_list)
    assert len(agents) == 5
    assert [a.role for a in agents] == ["role_0", "role_0", "role_1", "role_1", "role_1"]

def test_swarm_config_serialization_limit():
    # 1. Under limit (<= 1000)
    agents_small = [Agent("role_0", "prompt_0") for _ in range(5)]
    config_small = SwarmConfig(agents_small)
    res_small = config_small.dict()
    assert isinstance(res_small, dict)
    assert len(res_small["swarm"]) == 5
    assert all(isinstance(a, dict) and not isinstance(a, Agent) for a in res_small["swarm"])
    
    # 2. Over limit (> 1000)
    agents_large = [Agent(f"role_{i}", f"prompt_{i}") for i in range(1005)]
    config_large = SwarmConfig(agents_large)
    res_large = config_large.dict()
    assert isinstance(res_large, dict)
    assert len(res_large["swarm"]) == 1005
    # For large lists, it should return self.swarm directly (list of Agent objects)
    assert all(isinstance(a, Agent) for a in res_large["swarm"])

# ==========================================
# Gaps 1 & 2: Dimensioning Input Validation
# ==========================================

def test_dimensioning_invalid_vector_type():
    """Assert TypeErrors are raised for invalid input types."""
    # requirement_vector is not a list
    with pytest.raises(TypeError, match="requirement_vector must be a list"):
        dimension_swarm("not a list", [[1.0]])

    # transition_matrix is not a list
    with pytest.raises(TypeError, match="transition_matrix must be a list"):
        dimension_swarm([1.0], "not a list")

def test_dimensioning_invalid_matrix_row_type():
    """Assert transition matrix rows must be lists."""
    with pytest.raises(TypeError, match="transition_matrix must be a 2D list"):
        dimension_swarm([1.0, 2.0], [[1.0, 2.0], 2.0])

def test_dimensioning_boolean_elements():
    """Assert booleans are rejected as non-numeric in both inputs."""
    # bool in requirement_vector
    with pytest.raises(TypeError, match="requirement_vector elements must be numeric, not bool"):
        dimension_swarm([True, 1.0], [[1.0, 0.0]])

    # bool in transition_matrix
    with pytest.raises(TypeError, match="transition_matrix elements must be numeric, not bool"):
        dimension_swarm([1.0], [[False]])

def test_dimensioning_partially_empty_inputs():
    """Assert ValueErrors are raised when one of the inputs is empty."""
    with pytest.raises(ValueError, match="Inputs cannot be empty"):
        dimension_swarm([], [[1.0]])
        
    with pytest.raises(ValueError, match="Inputs cannot be empty"):
        dimension_swarm([1.0], [])


# ==========================================
# Gap 3: Property Setters & model_dump
# ==========================================

def test_agent_property_setters():
    """Test setters for Agent role and prompt properties."""
    agent = Agent("initial_role", "initial_prompt")
    
    # Test role setter
    agent.role = "updated_role"
    assert agent.role == "updated_role"
    assert agent["role"] == "updated_role"
    
    # Test prompt setter
    agent.prompt = "updated_prompt"
    assert agent.prompt == "updated_prompt"
    assert agent["prompt"] == "updated_prompt"

def test_swarm_config_property_setter():
    """Test setter for SwarmConfig swarm property."""
    agent_1 = Agent("role_1", "prompt_1")
    agent_2 = Agent("role_2", "prompt_2")
    config = SwarmConfig([agent_1])
    
    # Update via setter
    config.swarm = [agent_1, agent_2]
    assert config.swarm == [agent_1, agent_2]
    assert config["swarm"] == [agent_1, agent_2]

def test_swarm_config_model_dump():
    """Verify SwarmConfig.model_dump acts as an alias to dict()."""
    agents = [Agent("role_0", "prompt_0") for _ in range(5)]
    config = SwarmConfig(agents)
    
    dumped = config.model_dump()
    assert isinstance(dumped, dict)
    assert "swarm" in dumped
    assert len(dumped["swarm"]) == 5
    assert all(isinstance(a, dict) and not isinstance(a, Agent) for a in dumped["swarm"])


# ==========================================
# Edge Cases: LazyList Advanced Slicing
# ==========================================

def test_lazy_list_advanced_slicing():
    """Test LazyList slicing behavior with steps and negative indexing."""
    prefix_sums = [2, 5]
    total_count = 5
    lazy_list = LazyList(prefix_sums, total_count)
    
    # Slice with step
    sliced_step = lazy_list[::2]
    assert len(sliced_step) == 3
    assert [a.role for a in sliced_step] == ["role_0", "role_1", "role_1"]
    
    # Reverse slice
    reversed_slice = lazy_list[::-1]
    assert len(reversed_slice) == 5
    assert [a.role for a in reversed_slice] == ["role_1", "role_1", "role_1", "role_0", "role_0"]
    
    # Negative bounds slice
    neg_bounds_slice = lazy_list[-3:-1]
    assert len(neg_bounds_slice) == 2
    assert [a.role for a in neg_bounds_slice] == ["role_1", "role_1"]


# ==========================================
# Vulnerability Demos: Mutations & NaN/Inf
# ==========================================

def test_lazy_list_mutation_vulnerability():
    """Demonstrate how mutations are silently ignored or inconsistent in LazyList."""
    prefix_sums = [2, 5]
    total_count = 5
    lazy_list = LazyList(prefix_sums, total_count)
    
    # Try modifying an element in place (which raises IndexError because of empty base list)
    with pytest.raises(IndexError):
        lazy_list[0] = Agent("mutated_role", "mutated_prompt")
    
    # Appending doesn't adjust _total_count
    lazy_list.append(Agent("extra", "extra"))
    assert len(lazy_list) == 5  # Length is still 5
    with pytest.raises(IndexError):
        _ = lazy_list[5]        # Can't access the appended item

def test_agent_deletion_vulnerability():
    """Demonstrate how dictionary deletions crash property getters."""
    agent = Agent("role_0", "prompt_0")
    del agent["role"]
    
    with pytest.raises(KeyError):
        _ = agent.role

def test_dimensioning_float_edge_cases():
    """Verify behavior of NaN and Infinity in requirements / matrix."""
    # NaN values
    with pytest.raises((ValueError, TypeError)):
        dimension_swarm([float('nan')], [[1.0]])

    # Infinity values
    with pytest.raises((OverflowError, ValueError, TypeError)):
        dimension_swarm([1.0], [[float('inf')]])
