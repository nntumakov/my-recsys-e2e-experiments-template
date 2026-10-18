"""Hydra creates only the explicitly registered study variants."""

from collections.abc import Mapping, Sequence
from typing import cast

from hydra.utils import instantiate
from omegaconf import DictConfig, OmegaConf

from src.components.reporting.exporters.abstract import BaseExporter
from src.core.registry import BasicTransformerRegistry
from src.core.transformers.study import StudySearchTransformer
from src.domain import JSONValue
from src.domain.request.abstract import BaseFilter
from src.domain.serialization import fingerprint
from src.experiments.research.filters import StudyFilterBindings
from src.experiments.research.study import StudyExperiment


def _configuration_identity(value: object) -> JSONValue:
    """Credentials and connection URLs have no place in study artifacts or hashes."""
    if isinstance(value, Mapping):
        return {
            str(k): _configuration_identity(v)
            for k, v in value.items()
            if k not in {"authorization", "api_key", "url", "cache_dir"}
        }
    if isinstance(value, (list, tuple)):
        return [_configuration_identity(v) for v in value]
    return cast(JSONValue, value)


def study_experiment(
    variant_definitions: Mapping[str, Mapping[str, object]],
    variants: Sequence[str],
    bindings: Mapping[str, Mapping[str, object]],
    exporters: Sequence[Mapping[str, object]] = (),
    *,
    context: Mapping[str, JSONValue] | None = None,
    filters: Sequence[Mapping[str, object]] = (),
    comparability: Mapping[str, JSONValue] | None = None,
    text: str | None = None,
    input_path: str | None = None,
) -> StudyExperiment:
    if not variants or isinstance(variants, str) or len(set(variants)) != len(variants):
        raise ValueError("Select unique study variants to register")
    if not set(variants) <= set(variant_definitions):
        raise ValueError("Unknown study variant definition")
    entries = {}
    selected = {}
    for name in variants:
        definition = variant_definitions[name]
        if isinstance(definition, DictConfig):
            definition = cast(
                Mapping[str, object], OmegaConf.to_container(definition, resolve=True)
            )
        selected[name] = definition
        leaf = instantiate(definition, _convert_="all")
        if not isinstance(leaf, StudySearchTransformer):
            raise ValueError("Study definition requires a typed search transformer")
        entries[name] = leaf
    return StudyExperiment(
        BasicTransformerRegistry(entries),
        variants,
        StudyFilterBindings(bindings),
        exporters=[
            cast(BaseExporter, instantiate(e, _convert_="all")) for e in exporters
        ],
        context=context,
        filters=[cast(BaseFilter, instantiate(f, _convert_="all")) for f in filters],
        comparability=comparability,
        text=text,
        input_path=input_path,
        configuration_identity=fingerprint(_configuration_identity(selected)),
    )
