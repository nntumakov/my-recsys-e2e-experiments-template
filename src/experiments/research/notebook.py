"""Notebook composition and the current successful snapshot have explicit owners."""

import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from hydra import compose, initialize_config_dir
from hydra.utils import instantiate
from omegaconf import DictConfig

from src.components.reporting.artifacts.study import SearchStudyArtifact
from src.components.reporting.exporters.json import JSONExporter
from src.domain import JSONValue
from src.experiments.abstract import BaseExperiment
from src.experiments.research.study import StudyExperiment

CONFIG_ROOT = Path(__file__).resolve().parents[3] / "configs"


def compose_site_config(
    site_config: str | Path,
    overrides: Sequence[str] = (),
    *,
    selection: str = "manual",
    config_root: str | Path = CONFIG_ROOT,
) -> DictConfig:
    """Compose a site file against repository groups without resolving credentials."""
    site = Path(site_config).resolve()
    if not site.is_file() or site.suffix not in {".yaml", ".yml"}:
        raise ValueError("Supply an existing study site YAML file")
    if isinstance(overrides, str) or any(
        not isinstance(value, str) for value in overrides
    ):
        raise ValueError("Hydra overrides must be a sequence of strings")
    if selection not in {"manual", "batch", "interactive", "index", "chunk-index"}:
        raise ValueError("Unknown study experiment selection")
    with initialize_config_dir(config_dir=str(site.parent), version_base="1.3"):
        return compose(
            config_name=site.stem,
            overrides=[
                f"hydra.searchpath=[{Path(config_root).resolve().as_uri()}]",
                *overrides,
                f"experiments/vacancy-search/study@experiment={selection}",
                "experiment.exporters=[]",
            ],
        )


def compose_site_experiment(
    site_config: str | Path,
    overrides: Sequence[str] = (),
    *,
    selection: str = "manual",
    config_root: str | Path = CONFIG_ROOT,
) -> BaseExperiment:
    config = compose_site_config(
        site_config, overrides, selection=selection, config_root=config_root
    )
    experiment = instantiate(config.experiment, _convert_="all")
    if not isinstance(experiment, BaseExperiment):
        raise ValueError("Study configuration did not construct an experiment")
    return experiment


class StudyNotebookSession:
    def __init__(self, study: StudyExperiment) -> None:
        if not isinstance(study, StudyExperiment):
            raise ValueError("Notebook session requires a manual study experiment")
        self.study = study
        self.current: SearchStudyArtifact | None = None

    def preview(
        self,
        text: str,
        *,
        variants: Sequence[str] | None = None,
        filter_values: Mapping[str, JSONValue] | None = None,
        context: Mapping[str, JSONValue] | None = None,
    ) -> dict[str, JSONValue]:
        return self.study.preview(
            text,
            variants=variants,
            context=context,
            filters=self.study.bindings.terms(filter_values or {}),
        )

    def compare(
        self,
        text: str,
        *,
        variants: Sequence[str] | None = None,
        filter_values: Mapping[str, JSONValue] | None = None,
        context: Mapping[str, JSONValue] | None = None,
        reference_variant: str | None = None,
    ) -> SearchStudyArtifact:
        # This also covers errors in input binding before any engine is called.
        self.current = None
        result = self.study.compare(
            text,
            variants=variants,
            context=context,
            filters=self.study.bindings.terms(filter_values or {}),
            reference_variant=reference_variant,
        )
        self.current = result
        return result

    def require_current(self) -> SearchStudyArtifact:
        if self.current is None:
            raise RuntimeError(
                "No successful current result; run compare before reading or saving"
            )
        return self.current

    def save(self, output_root: str | Path = "outputs/search-study") -> Path:
        artifact = self.require_current()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", artifact.run_id):
            raise ValueError("Snapshot run_id is not a safe output directory name")
        destination = Path(output_root) / artifact.run_id / "search-study.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        JSONExporter(str(destination)).export(artifact)
        return destination
