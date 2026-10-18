"""Contract checks for the authored jobboard starter content."""

import json
from pathlib import Path

import yaml
from hydra import compose, initialize_config_dir
from hydra.utils import instantiate

from src.core.transformers.opensearch.jobboard.query import compile_query
from src.domain.request.filters import QueryFilter
from src.domain.request.request import Request

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = ROOT / "configs"
LEXICAL = CONFIGS / "jobboard/lexical"
SELECT_EXPERIMENT = [
    "~experiments/vacancy-search/interactive@experiment",
    "+experiments/vacancy-search/jobboard/lexical@experiment=001-interactive",
    "jobboard/lexical/source@jobboard.source=fixture",
    "connection.url=http://localhost:1234",
    "experiment.exporters.0.path=/tmp/jobboard-content.json",
]


def load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def composed(overrides=()):
    with initialize_config_dir(config_dir=str(CONFIGS), version_base="1.3"):
        return compose(config_name="main", overrides=[*SELECT_EXPERIMENT, *overrides])


def test_starter_lexicons_are_complete_and_well_formed():
    expected = {
        "roles": 27,
        "skills": 30,
        "seniority": 7,
        "work_modes": 3,
        "locations": 6,
    }
    all_ids: set[str] = set()
    for category, count in expected.items():
        entries = load_yaml(LEXICAL / "lexicons" / category / "ru-en.yaml")["entries"]
        assert len(entries) == count
        for entry in entries:
            assert set(entry) == {
                "id",
                "canonical",
                "surfaces",
                "alternatives",
                "ambiguous_surfaces",
                "requires_any",
            }
            assert entry["id"] not in all_ids
            all_ids.add(entry["id"])
            assert entry["canonical"] in entry["surfaces"]
            assert len({value.casefold() for value in entry["surfaces"]}) == len(
                entry["surfaces"]
            )
            ambiguous = {value.casefold() for value in entry["ambiguous_surfaces"]}
            alternatives = {row["text"].casefold() for row in entry["alternatives"]}
            assert ambiguous.isdisjoint(alternatives)
            assert all(
                row["relation"] in {"curated_equivalent", "curated_translation"}
                for row in entry["alternatives"]
            )

    assert len(all_ids) == 73
    protected = load_yaml(LEXICAL / "lexicons/protected/default.yaml")["terms"]
    assert protected == [
        "C++",
        "C#",
        ".NET",
        "Node.js",
        "Vue.js",
        "React.js",
        "1C",
        "1С",
        "C",
        "Go",
        "R",
        "SQL",
        "QA",
        "ML",
        "AI",
        "JS",
        "TS",
        "SRE",
        "K8s",
    ]


def test_parser_semantics_cover_ambiguity_boundaries_and_syntax():
    cfg = composed()
    parser = instantiate(cfg.jobboard.parser, _convert_="all")

    def hints(query):
        result = parser.parse(query)
        return result, [
            (hint.kind, hint.canonical, hint.status) for hint in result.hints
        ]

    result, found = hints("Go")
    assert result.structured_enabled
    assert ("skill", "Go", "ambiguous") in found
    assert ("skill", "Go", "unambiguous") in hints("Go developer")[1]
    assert not hints("Google")[1]

    _, java = hints("JavaScript")
    assert {row[1] for row in java} == {"JavaScript"}
    _, punctuation = hints("C++ C# .NET")
    assert {row[1] for row in punctuation} >= {"C++", "C#", ".NET"}
    _, pm = hints("PM")
    assert {(row[1], row[2]) for row in pm} == {
        ("project manager", "ambiguous"),
        ("product manager", "ambiguous"),
    }
    _, ba = hints("BA")
    assert ba == [("role", "business analyst", "ambiguous")]
    _, resolved_ba = hints("business analyst BA")
    assert any(
        row == ("role", "business analyst", "unambiguous") for row in resolved_ba
    )

    entry, entry_hints = hints("без опыта аналитик")
    assert entry.structured_enabled
    assert any(
        row == ("seniority", "entry level", "unambiguous") for row in entry_hints
    )
    for query in ("python или java", "не продажи", "без ночных смен"):
        assert not parser.parse(query).structured_enabled

    _, attributes = hints("Senior Python developer remote Moscow")
    assert {row[1] for row in attributes} >= {"senior", "Python", "remote", "Москва"}
    assert not any(row[1] == "recruiter" for row in attributes)


def test_parser_ablation_and_retrieval_profiles_hydra_instantiate():
    cfg = composed()
    assert instantiate(cfg.jobboard.lexical_parser, _convert_="all").stages == ()
    assert len(instantiate(cfg.jobboard.parser, _convert_="all").stages) == 5
    role_skill_parser = instantiate(
        composed(
            ("jobboard/lexical/parsing@jobboard.parser=roles_skills",)
        ).jobboard.parser,
        _convert_="all",
    )
    assert [stage.kind for stage in role_skill_parser.stages] == ["role", "skill"]

    profiles = {}
    for name in (
        "lexical",
        "parsed",
        "fuzzy",
        "no_phrase",
        "no_aliases",
        "no_topical_gate",
        "strict_coverage",
    ):
        overrides = (
            (f"jobboard/lexical/retrieval@jobboard.retrieval.parsed={name}",)
            if name != "lexical"
            else ()
        )
        profile = composed(overrides).jobboard.retrieval.parsed
        value = instantiate(profile, _convert_="all")
        profiles[name] = value
        assert value.field_weights == {}
        assert value.default_field_weight == 1
        assert value.title_field_weight == 4
    fuzzy = instantiate(
        composed(
            ("jobboard/lexical/retrieval@jobboard.retrieval.parsed=fuzzy",)
        ).jobboard.retrieval.parsed,
        _convert_="all",
    )
    assert fuzzy.fuzzy.enabled and fuzzy.fuzzy.distance == 1

    parser = instantiate(cfg.jobboard.parser, _convert_="all")
    source = instantiate(cfg.jobboard.source, _convert_="all")

    def body(profile, query):
        return compile_query(
            parser.parse(query),
            Request((QueryFilter(query),)),
            {},
            source,
            profile,
            100,
        )

    def walk(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from walk(child)
        elif isinstance(value, list):
            for child in value:
                yield from walk(child)

    phrase_query = "python backend developer"
    parsed_body = body(profiles["parsed"], phrase_query)
    no_phrase_body = body(profiles["no_phrase"], phrase_query)
    assert any(
        clause.get("match_phrase", {}).get("title", {}).get("query") == phrase_query
        for clause in walk(parsed_body)
    )
    assert not any(
        clause.get("match_phrase", {}).get("title", {}).get("query") == phrase_query
        for clause in walk(no_phrase_body)
    )

    alias_query = "backend engineer"
    assert "backend developer" in str(body(profiles["parsed"], alias_query))
    assert "backend developer" not in str(body(profiles["no_aliases"], alias_query))
    gated = body(profiles["parsed"], "python")
    ungated = body(profiles["no_topical_gate"], "python")

    def has_topical_gate(value):
        return any(
            node.get("constant_score", {}).get("boost") == 0 for node in walk(value)
        )

    assert has_topical_gate(gated)
    assert not has_topical_gate(ungated)

    multi = "backend python JavaScript Docker Kubernetes"
    default_coverage = body(profiles["parsed"], multi)
    strict_coverage = body(profiles["strict_coverage"], multi)

    def thresholds(query_body):
        return [
            node["minimum_should_match"]
            for node in walk(query_body)
            if "minimum_should_match" in node and "should" in node
        ]

    assert 4 in thresholds(default_coverage)
    assert 5 in thresholds(strict_coverage)

    fuzzy_body = body(profiles["fuzzy"], "python developr")
    parsed_body = body(profiles["parsed"], "python developr")
    assert '"fuzziness": 1' in json.dumps(fuzzy_body)
    assert '"fuzziness": 1' not in json.dumps(parsed_body)


def test_synthetic_corpus_and_cases_are_closed_and_complete():
    documents = json.loads(
        (ROOT / "tests/fixtures/jobboard/vacancies.json").read_text()
    )
    assert [row["_id"] for row in documents] == [f"j{i:02}" for i in range(1, 21)]
    assert all(set(row) == {"_id", "_source"} for row in documents)
    assert all(
        set(row["_source"]) == {"title", "description", "city", "remote"}
        for row in documents
    )
    assert all(isinstance(row["_source"]["remote"], bool) for row in documents)

    cases = load_yaml(LEXICAL / "cases/synthetic.yaml")
    assert len(cases) == 24
    ids = {row["_id"] for row in documents}
    assert all(set(case["relevance"]) == ids for case in cases)
    by_key = {case["key"]: case for case in cases}
    assert by_key["q01"]["relevance"]["j01"] == 3
    assert by_key["q24"]["relevance"] == {identifier: 0 for identifier in sorted(ids)}
    explicit_cases = load_yaml(LEXICAL / "cases/explicit-filters.yaml")
    explicit_by_key = {case["key"]: case for case in explicit_cases}
    explicit = explicit_by_key["explicit-filter"]["data"]["filters"]
    assert explicit[0]["text"] == "python"
    assert explicit[1]["_target_"].endswith("TermFilter")
    assert explicit[1]["field"] == "remote" and explicit[1]["value"] is True
    property_case = explicit_by_key["user-property-filter"]["data"]
    assert property_case["context"] == {"selected_city": "moscow"}
    assert property_case["filters"][1]["_target_"].endswith("UserPropertyFilter")

    smoke = load_yaml(LEXICAL / "cases/fixture-smoke.yaml")
    assert [case["key"] for case in smoke] == ["q01", "q02"]
    assert all(set(case["relevance"]) == ids for case in smoke)
