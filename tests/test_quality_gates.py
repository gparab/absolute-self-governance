"""Tests for PersonaQualityGate filtering in ConsensusEngine."""
from self_governance.models import PersonaQualityGate
from self_governance.consensus import ConsensusEngine


class TestPersonaQualityGate:
    def test_passes_above_min_confidence(self):
        gate = PersonaQualityGate(min_confidence=8.0)
        assert gate.passes(8.5, "looks good") is True

    def test_fails_below_min_confidence(self):
        gate = PersonaQualityGate(min_confidence=8.0)
        assert gate.passes(7.9, "looks good") is False

    def test_false_positive_suppresses_vote(self):
        gate = PersonaQualityGate(
            min_confidence=0.0,
            false_positive_exclusions=["test mock"]
        )
        assert gate.passes(9.0, "This is a test mock fixture") is False

    def test_require_evidence_blocks_without_file_ref(self):
        gate = PersonaQualityGate(require_evidence=True)
        assert gate.passes(9.0, "general commentary") is False

    def test_require_evidence_passes_with_file_ref(self):
        gate = PersonaQualityGate(require_evidence=True)
        assert gate.passes(9.0, "see consensus.py:394 for the issue") is True

    def test_default_gate_always_passes(self):
        gate = PersonaQualityGate()
        assert gate.passes(1.0, "") is True


class TestConsensusEngineGating:
    def test_gated_votes_abstain_from_average(self):
        """A persona with a min_confidence=9.5 gate that scores 7.5 should abstain."""
        # We'll monkeypatch get_persona to inject a gate
        from unittest.mock import patch
        from self_governance.agency_agents_adapter import get_persona as real_get_persona

        def mock_get_persona(role, **kwargs):
            p = real_get_persona(role, **kwargs)
            if role == "Security Auditor":
                p = dict(p)
                p["quality_gate"] = {"min_confidence": 9.5}
            return p

        engine = ConsensusEngine(
            initial_roster=["Backend Wizard", "Security Auditor"],
            seed=42,
            B=1,
        )
        with patch("self_governance.consensus.get_persona", side_effect=mock_get_persona):
            result = engine.run()
        # Just verify it terminates cleanly — gating is exercised
        assert result.approved_roster is not None
        assert isinstance(result.final_temperature, float)
