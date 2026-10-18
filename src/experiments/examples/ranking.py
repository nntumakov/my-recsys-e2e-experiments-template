"""Compose the offline ranking experiment from Python, without CLI or exporters."""

import json
from collections.abc import Sequence
from pathlib import Path

from hydra import compose, initialize_config_dir
from hydra.utils import instantiate

from src.components.reporting.artifacts.comparison import ComparisonArtifact
from src.experiments.testing.compare import CompareExperiment


def compare(overrides: Sequence[str] = ()) -> ComparisonArtifact:
    config_dir = Path(__file__).resolve().parents[3] / "configs"
    with initialize_config_dir(config_dir=str(config_dir), version_base="1.3"):
        cfg = compose(
            config_name="ranking",
            overrides=[*overrides, "experiment.exporters=[]"],
        )
        experiment: CompareExperiment = instantiate(cfg.experiment, _convert_="all")
    return experiment.run()


if __name__ == "__main__":
    artifact = compare()
    print(json.dumps(artifact.to_dict(), ensure_ascii=False, indent=2))
