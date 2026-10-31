"""Companion worker (knowledge queue): knowledge research, figure descriptions and episode scripts,
the three companion stages CoursePipelineWorkflow runs after translation (charter §4)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from b2c_orchestration import knowledge_worker as kw

ENV = {
    "B2C_KNOWLEDGE_EXTRACTOR_BASE_URL": "https://openrouter.ai/api/v1",
    "B2C_KNOWLEDGE_EXTRACTOR_MODEL_ID": "deepseek/deepseek-v4-flash-0731",
    "B2C_KNOWLEDGE_EXTRACTOR_ALLOW_REMOTE": "1", "B2C_KNOWLEDGE_EXTRACTOR_API_KEY_FILE": "",
    "B2C_KNOWLEDGE_JUDGE_BASE_URL": "https://api.inceptionlabs.ai/v1",
    "B2C_KNOWLEDGE_JUDGE_MODEL_ID": "mercury-2", "B2C_KNOWLEDGE_JUDGE_FAMILY": "inception",
    "B2C_KNOWLEDGE_JUDGE_ALLOW_REMOTE": "1", "B2C_KNOWLEDGE_JUDGE_API_KEY_FILE": "",
}


@pytest.fixture
def env(tmp_path):
    key = tmp_path / "k"
    key.write_text("secret\n")
    return {**ENV, "B2C_KNOWLEDGE_EXTRACTOR_API_KEY_FILE": str(key), "B2C_KNOWLEDGE_JUDGE_API_KEY_FILE": str(key)}


class Results:
    def fetch_run(self, run_id):
        t = SimpleNamespace(decision="accepted")
        return [SimpleNamespace(unit_id="bk:u00001", translation=t, drafts=[{"text": "x"}])]


def test_companion_registers_the_three_stages(env):
    workflows, activities = kw.build_companion(store=object(), cache=None, promotion=None, env=env,
                                               runtime=object(), memory=None, result_store=Results())
    assert [w.__temporal_workflow_definition.name for w in workflows] == [
        "BookKnowledgeWorkflow", "BuildFigureDescriptionsWorkflow", "BuildEpisodeScriptsWorkflow"]
    names = sorted(a.__temporal_activity_definition.name for a in activities)
    assert len(names) == 6 and len(set(names)) == 6


def test_episode_inputs_read_the_book_run_from_the_result_store(env):
    _, activities = kw.build_companion(store=object(), cache=None, promotion=None, env=env,
                                       runtime=object(), memory=None, result_store=Results())
    episodes = next(a.__self__ for a in activities if type(a.__self__).__name__ == "EpisodeActivities")
    assert episodes.book_results("r")["bk:u00001"].decision == "accepted"
    assert episodes.book_drafts("r")["bk:u00001"] == [{"text": "x"}]
    assert episodes.generator.family == "deepseek" and episodes.judge.family == "inception"


def test_generator_and_judge_must_be_different_families(env):
    same = {**env, "B2C_KNOWLEDGE_JUDGE_FAMILY": "deepseek"}
    with pytest.raises(ValueError):
        kw.build_companion(store=object(), cache=None, promotion=None, env=same, runtime=object(), memory=None,
                           result_store=Results())
