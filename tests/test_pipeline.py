import json
import threading

import pytest

from cutweave.core.orchestrator import (
    PipelineCancelled, PipelineError, PipelineOrchestrator, PipelineStage,
)


def stages_for(tmp_path, calls, *, cancel_after_visual=None):
    def stage(stage_id, dependencies=(), params=lambda context: {}):
        def execute(context, dependency_artifacts, cancel_event, progress):
            calls.append(stage_id)
            progress(f"building {stage_id}")
            path = tmp_path / "artifacts" / f"{stage_id}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "stage": stage_id,
                "params": params(context),
                "dependencies": sorted(dependency_artifacts),
            }), encoding="utf-8")
            if stage_id == "visual" and cancel_after_visual is not None:
                cancel_after_visual.set()
            return path

        return PipelineStage(stage_id, "1", execute, dependencies,
                             parameters=params)

    return [
        stage("visual", params=lambda context: {"crop": context["crop"]}),
        stage("composition", ("visual",), lambda context: {"captions": context["captions"]}),
        stage("finishing", ("composition",), lambda context: {"music": context["music"]}),
    ]


def test_pipeline_runs_in_dependency_order_and_reuses_valid_checkpoints(tmp_path):
    calls = []
    orchestrator = PipelineOrchestrator(tmp_path)
    stages = stages_for(tmp_path, calls)
    context = {"crop": "vertical", "captions": 3, "music": "bed.wav"}

    results = orchestrator.run(stages, context)
    assert list(results) == ["visual", "composition", "finishing"]
    assert calls == ["visual", "composition", "finishing"]
    resumed = orchestrator.run(stages, context)
    assert calls == ["visual", "composition", "finishing"]
    assert resumed["finishing"].path == results["finishing"].path


def test_parameter_change_reruns_only_affected_stages_and_downstream(tmp_path):
    calls = []
    orchestrator = PipelineOrchestrator(tmp_path)
    stages = stages_for(tmp_path, calls)
    orchestrator.run(stages, {"crop": "vertical", "captions": 3, "music": "bed.wav"})
    calls.clear()
    orchestrator.run(stages, {"crop": "vertical", "captions": 4, "music": "bed.wav"})
    assert calls == ["composition", "finishing"]


def test_target_runs_its_dependencies_but_skips_unrelated_later_stages(tmp_path):
    calls = []
    orchestrator = PipelineOrchestrator(tmp_path)
    result = orchestrator.run(stages_for(tmp_path, calls),
                              {"crop": "square", "captions": 0, "music": None}, targets=("composition",))
    assert list(result) == ["visual", "composition"]
    assert calls == ["visual", "composition"]


def test_cancelled_stage_is_checkpointed_and_resumes_on_next_run(tmp_path):
    calls = []
    orchestrator = PipelineOrchestrator(tmp_path)
    cancelled = threading.Event()
    stages = stages_for(tmp_path, calls, cancel_after_visual=cancelled)
    context = {"crop": "vertical", "captions": 3, "music": None}
    with pytest.raises(PipelineCancelled):
        orchestrator.run(stages, context, cancel_event=cancelled)
    state = json.loads(orchestrator.state_path.read_text(encoding="utf-8"))
    assert state["stages"]["visual"]["status"] == "cancelled"

    calls.clear()
    result = orchestrator.run(stages_for(tmp_path, calls), context)
    assert calls == ["visual", "composition", "finishing"]
    assert result["finishing"].path.is_file()


def test_invalid_or_cyclic_stage_graph_is_rejected(tmp_path):
    orchestrator = PipelineOrchestrator(tmp_path)

    def execute(*_args):
        raise AssertionError("Une structure invalide ne doit pas exécuter de callback")

    a = PipelineStage("a", "1", execute, ("b",))
    b = PipelineStage("b", "1", execute, ("a",))
    with pytest.raises(PipelineError, match="cyclique"):
        orchestrator.run((a, b), {})
