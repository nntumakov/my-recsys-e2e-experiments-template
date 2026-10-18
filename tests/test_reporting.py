import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.components.reporting.artifacts.search import SearchArtifact
from src.components.reporting.exporters.json import JSONExporter
from src.domain import JSONValue
from src.experiments.abstract import BaseExperiment


def test_artifact_owns_snapshot_and_export_does_not_overwrite(tmp_path):
    tags: list[JSONValue] = ["ru"]
    records: list[dict[str, JSONValue]] = [
        {"query": "python", "items": [{"fields": {"tags": tags}}]}
    ]
    artifact = SearchArtifact(records)
    tags.append("en")
    result = artifact.to_dict()
    result.clear()
    path = tmp_path / "searches.json"
    exporter = JSONExporter(str(path))
    exporter.export(artifact)
    expected = {
        "searches": [{"query": "python", "items": [{"fields": {"tags": ["ru"]}}]}]
    }
    assert json.loads(path.read_text()) == expected
    with pytest.raises(FileExistsError):
        exporter.export(SearchArtifact([]))
    assert json.loads(path.read_text()) == expected
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("operation", ["fsync", "link"])
def test_failed_export_leaves_no_partial_file(tmp_path, monkeypatch, operation):
    def fail(*args):
        raise OSError("write failed")

    monkeypatch.setattr(
        f"src.components.reporting.exporters.publication.os.{operation}", fail
    )
    with pytest.raises(OSError):
        JSONExporter(str(tmp_path / "searches.json")).export(SearchArtifact([]))
    assert list(tmp_path.iterdir()) == []


def test_serialization_failure_precedes_writes(tmp_path):
    artifact = Mock()
    artifact.to_dict.return_value = {"invalid": float("nan")}
    with pytest.raises(ValueError):
        JSONExporter(str(tmp_path / "searches.json")).export(artifact)
    assert list(tmp_path.iterdir()) == []


def test_export_chain_stops_at_failure():
    class Experiment(BaseExperiment):
        def run(self):
            return SearchArtifact([])

    first, second, third = Mock(), Mock(), Mock()
    second.export.side_effect = OSError("disk full")
    with pytest.raises(OSError):
        Experiment([first, second, third])._wrapped_run()
    first.export.assert_called_once()
    second.export.assert_called_once()
    third.export.assert_not_called()


@pytest.mark.parametrize("publish_fails", [False, True])
def test_cleanup_failure_preserves_publication_outcome(
    tmp_path, monkeypatch, caplog, publish_fails
):
    target = tmp_path / "searches.json"
    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "unlink", Mock(side_effect=PermissionError("cleanup")))
        if publish_fails:
            scoped.setattr(
                "src.components.reporting.exporters.publication.os.link",
                Mock(side_effect=FileExistsError("original")),
            )
            with pytest.raises(FileExistsError, match="original"):
                JSONExporter(str(target)).export(SearchArtifact([]))
        else:
            JSONExporter(str(target)).export(SearchArtifact([]))
    assert target.exists() is not publish_fails
    assert "Could not remove temporary export file" in caplog.text
