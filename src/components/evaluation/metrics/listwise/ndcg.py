from collections.abc import Mapping
from dataclasses import dataclass
from math import expm1, fsum, isfinite, log, log2

from src.domain import JSONValue
from src.domain.collection.serp import SERP

from .abstract import BaseListWiseMetric, BaseListWiseMetricReport


@dataclass(frozen=True)
class ListWiseNDCGReport(BaseListWiseMetricReport):
    value: float
    dcg: float
    idcg: float
    k: int

    def to_dict(self) -> dict[str, JSONValue]:
        return {"value": self.value, "dcg": self.dcg, "idcg": self.idcg, "k": self.k}


class ListWiseNDCG(BaseListWiseMetric):
    def __init__(self, k: int) -> None:
        if type(k) is not int or k <= 0:
            raise ValueError("NDCG cutoff must be a positive integer")
        self.k = k

    def eval(self, serp: SERP, relevance: Mapping[str, float]) -> ListWiseNDCGReport:
        ids = [item.id for item in serp.items]
        if len(set(ids)) != len(ids):
            raise ValueError("NDCG requires unique document ids")
        if any(
            type(grade) not in (int, float)
            or grade < 0
            or grade >= 1024
            or not isfinite(grade)
            for grade in relevance.values()
        ):
            raise ValueError("Relevance grades must be finite and in [0, 1024)")
        if any(identifier not in relevance for identifier in ids[: self.k]):
            raise ValueError("Missing relevance judgment in evaluated ranking")
        try:
            gains = {
                identifier: expm1(grade * log(2))
                for identifier, grade in relevance.items()
            }
            dcg = fsum(
                gains[identifier] / log2(rank + 1)
                for rank, identifier in enumerate(ids[: self.k], 1)
            )
            idcg = fsum(
                gain / log2(rank + 1)
                for rank, gain in enumerate(
                    sorted(gains.values(), reverse=True)[: self.k], 1
                )
            )
        except OverflowError as exc:
            raise ValueError("Relevance gains exceed supported numeric range") from exc
        if not isfinite(dcg) or not isfinite(idcg):
            raise ValueError("Nonfinite discounted gain")
        return ListWiseNDCGReport(dcg / idcg if idcg else 0.0, dcg, idcg, self.k)
