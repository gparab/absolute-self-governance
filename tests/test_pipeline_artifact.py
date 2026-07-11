"""Tests for PipelineArtifact chaining in the nudger."""
import json
import os
import tempfile


from self_governance.models import PipelineArtifact, PipelinePhase
from self_governance.nudger import (
    PIPELINE_ARTIFACT_FILE,
    append_pipeline_artifact,
    load_prior_artifacts,
)


class TestPipelineArtifactModel:
    def test_default_artifact_is_valid(self):
        a = PipelineArtifact()
        d = a.model_dump()
        assert d["phase"] == "build"
        assert d["approved_roster"] == []
        assert d["cycles_needed"] == 1
        assert "timestamp" in d

    def test_artifact_with_roster(self):
        a = PipelineArtifact(
            phase=PipelinePhase.REVIEW,
            approved_roster=["Backend Wizard", "QA Specialist"],
            decisions=["Use Redis for caching"],
            next_context="Completed review phase",
        )
        d = a.model_dump()
        assert d["phase"] == "review"
        assert len(d["approved_roster"]) == 2
        assert d["decisions"] == ["Use Redis for caching"]


class TestPipelineArtifactChain:
    def test_append_and_load_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Append two artifacts
            for i in range(2):
                a = PipelineArtifact(
                    approved_roster=[f"Agent{i}"],
                    next_context=f"Context {i}",
                )
                append_pipeline_artifact(tmpdir, a.model_dump())

            loaded = load_prior_artifacts(tmpdir)
            assert len(loaded) == 2
            assert loaded[0]["approved_roster"] == ["Agent0"]
            assert loaded[1]["next_context"] == "Context 1"

    def test_load_returns_empty_when_no_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = load_prior_artifacts(tmpdir)
            assert result == []

    def test_load_respects_max_prior_limit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            for i in range(10):
                a = PipelineArtifact(next_context=f"ctx{i}")
                append_pipeline_artifact(tmpdir, a.model_dump())

            loaded = load_prior_artifacts(tmpdir)
            assert len(loaded) <= 5  # _MAX_PRIOR_ARTIFACTS
            assert loaded[-1]["next_context"] == "ctx9"  # most recent last

    def test_artifact_jsonl_format(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            a = PipelineArtifact(next_context="hello")
            append_pipeline_artifact(tmpdir, a.model_dump())

            path = os.path.join(tmpdir, PIPELINE_ARTIFACT_FILE)
            with open(path) as f:
                lines = f.readlines()
            assert len(lines) == 1
            parsed = json.loads(lines[0])
            assert parsed["next_context"] == "hello"
