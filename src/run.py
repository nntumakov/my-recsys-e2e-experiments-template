import logging
from uuid import uuid4

import hydra
from hydra.utils import instantiate
from omegaconf import DictConfig, OmegaConf

from src.experiments.abstract import BaseExperiment

OmegaConf.register_new_resolver(
    "run_id", lambda: uuid4().hex, use_cache=True, replace=True
)


@hydra.main(config_path="../configs", config_name="main", version_base="1.3")
def run(cfg: DictConfig) -> None:
    phase = "configuration"
    try:
        experiment: BaseExperiment = instantiate(cfg.experiment, _convert_="all")
        phase = "experiment or export"
        experiment._wrapped_run()
    except KeyboardInterrupt:
        logging.getLogger(__name__).info(
            "Interrupted during %s; no session export", phase
        )
        raise SystemExit(130) from None
    except Exception as exc:
        # Hydra's default exception rendering can include resolved credentials.
        logging.getLogger(__name__).error(
            "Failed during %s (%s)", phase, type(exc).__name__
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    run()
