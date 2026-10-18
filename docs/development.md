# Текущий статус и проверка

## Поставка 009: матрица поиска и проработанный notebook

[Spec 009](specs/009-search-experiment-matrix.md) реализована локально: пять fulltext
и пять embedding-вариантов, явные scalar AND filters, независимые snapshots,
ручное сравнение, CLI/batch и подготовка whole/title/body/chunk generations.
По уточнению пользователя dense-варианты исследуют разные механизмы и представления
на одной pinned E5-base. Основной интерфейс —
[search-study.ipynb](../src/experiments/notebooks/search-study.ipynb): preview,
summary, side-by-side rank lists, union matrix с reference/deltas, полные карточки,
E5 winner passage, diagnostics и explicit no-overwrite save.

| Область | Владелец и local evidence |
| --- | --- |
| Logical bindings, preflight/admission, manual/batch/CLI и snapshots | `experiments/research/study.py`, `filters.py`, `composition.py`, `domain/study.py`; `tests/test_study_workflow.py` |
| F1–F5: core, bounded bonus, tiers, independent RRF | `core/transformers/opensearch/study.py`, `components/query/study.py`; `tests/test_study_fulltext.py` |
| E1–E4: same-pool exact scoring, multi-query, shared E4 encode | `core/transformers/qdrant/study.py`, `components/embedding/representations.py`; `tests/test_study_dense.py` |
| Strict common text cohort | `components/corpus/study.py`; `tests/test_study_cohort.py` |
| E5: UTF-8/token audit, source v2, full M read-back, exclusions/max | `components/embedding/chunks.py`, `integrations/qdrant/chunks.py`, `experiments/indexing/chunks.py`, `core/transformers/qdrant/chunks.py`; `tests/test_study_chunks.py` |
| Snapshot HTML, source-path conditions, cards and notebook session | `components/reporting/artifacts/study.py`, `experiments/research/notebook.py`; `tests/test_study_artifact.py`, `test_study_notebook.py` |
| 40 contrasts, 80 fictional cards, 11 boundaries, per-main mechanism controls | `configs/study/cases`, `tests/fixtures/study`, `tests/test_study_content.py` |

Составной fixture строит четыре маленьких синтетических поколения через настоящие
Hydra/index modules и локальный HTTP server. Он задаёт scores и UTF-8 token offsets;
BM25, tokenizer, GPU embedding и native Qdrant алгоритмы не исполняются.
Это evidence orchestration/формул/отказов, без утверждения о качестве поиска.
Реальные OpenSearch, Qdrant, TEI, Docker и модели на этом устройстве **не запускались**.
G01–G09 009 и незакрытые live gates 008 остаются **не выполнены**; удобство реального
Jupyter frontend отдельно проверяется на целевой машине по G09.

Локальные notebook code cells исполняются дважды без saved outputs; UI display
заменён только presentation stub. Default flags не выполняют writes, batch,
expensive token audit или export. Tests проверяют смену query/city/variants,
reference/rank deltas, полные карточки с HTML escaping, отсутствие HTTP при render,
сброс current при новой ошибке, retry, новую composition identity и no-overwrite save.
Во входном content pack сверхдлинная `s31-positive` намеренно превышает max64 E5:
этот pack целиком не объявляется готовым corpus без audit.

| Критерии 009 | Исполненный local verification path |
| --- | --- |
| L01–L02, L13, L15–L16 | Все десять Hydra leaves и четыре build wrappers через HTTP fixture; isolation/AND/strict type, pure preflight, all-generation admission, cohort mismatch до inference, late failure/retry, CLI EOF/error/Ctrl-C/atomic export |
| L03–L05 | Strict role/qualification coverage, scalar/text preference predicates, multiplier bound, disjoint tiers, RRF full-depth arithmetic, duplicate vote и second-branch failure в fulltext suite |
| L06–L08 | Native UUID has_id полного topical pool, α bound/mean/min, empty pool, changed fields/duplicate/missing hit, E3 union/suppression и E4 compatible sources/one encode в dense suite |
| L09–L12 | Unicode byte/word coverage, overlap/title cutoff, truncate=false и no-write preflight; N/M/v1-v2/read-back/publication; три concentrated вакансии до100unique, max/opportunity cases, поздняя iteration failure/exhaustion/retry в chunk suite |
| L14, N01–N09 | Все code cells дважды на Hydra→HTTP, source/city/variant changes, ranks/deltas/empty/source differences, full cards и escaping, no extra calls, current reset/retry, composition change и explicit no-overwrite save |
| L17, L19 | 40contrastcases/80cards/11boundaries, per-main control/difference/negative; полный прежний набор249queries×5fulltext previews сохраняет исходные outcomes |
| L18 | Общие Ruff/format/ty/pytest, Markdown target/anchor и code-example checks, notebook AST/empty outputs, study YAML и documentation whitespace |

Три независимых review проверяют Standards и Spec, включая modules других авторов.
Протокол и исправления — [review 009](reviews/2026-10-10-search-experiment-matrix.md).
Полный `.venv/bin/pytest -q` — **1014 passed** (544.16 s).
После него уточнена explicit raw description binding fulltext. Адресный прогон
на итоговом дереве — **35 passed** (35.75 s): fulltext suite целиком, all-ten
Hydra→HTTP comparison, все notebook cells дважды и CLI/batch wrappers.
Отдельно materialized custom `body` binding проверен во всех пяти fulltext
variants через compose/preview с запрещённым HTTP. Общие `.venv/bin/ruff check .`,
`.venv/bin/ruff format --check .`, `.venv/bin/ty check` проходят.
Проверены 114 локальных Markdown targets, пять anchors, шесть YAML/Python примеров,
все study YAML, AST и отсутствие outputs notebook, read-only CLI `--cfg job`.
`git diff --check` и staged whitespace проходят.
Используется существующая `.venv` Python 3.12.3; Poetry отсутствует в PATH,
manifest/lockfile не менялись. Установок, image/model downloads и heavy builds нет.
На диске при реализации осталось около 3.3 GiB (97% занято).

Owner contracts — в [architecture](architecture.md), рабочий site/notebook/CLI
сценарий — в [README](../README.md). Study schema отдельная; прежние defaults,
registry/payload и export formats 001–008 сохраняются.

## Поставка 008: векторный поиск и самостоятельная инфраструктура

[Spec 008](specs/008-vector-search.md) финализирована 2026-10-10 по запросу
пользователя. Зафиксированы TEI/E5-base, внешний user-owned Qdrant, конфигурация
Hydra, query/document encoding, immutable collection generations, publication/
recovery и отдельные local/live критерии приёмки. Исследовательские основания
и первичные источники доступны из spec.

Реализованы encoding/reader, Qdrant transport/source, создание и
проверка нового поколения, vector transformer, Hydra wrappers, TEI launcher и
[notebook](../src/experiments/notebooks/vector.ipynb). Локальные критерии L01–L21
подтверждены tests и общими gates ниже. Image/model downloads, GPU inference и реальный Qdrant
не запускались. G01–G06 остаются **не выполнены**.

| Область | Владелец и локальные проверки |
| --- | --- |
| Encoding profile, immutable vectors и canonical identities | `src/domain/embedding.py`, `serialization.py`; `tests/test_vector_encoding.py` |
| JSON snapshot, recipe, prefixes, byte/batch limits и TEI responses | `src/components/corpus`, `components/embedding`, `integrations/embedding/client.py`; `tests/test_vector_encoding.py` |
| Native Qdrant, scalar filters, descriptor и atomic SERP | `src/integrations/qdrant`, `core/transformers/qdrant`; `tests/test_vector_qdrant.py` |
| Fresh generation, full read-back, ready publication и receipt | `src/experiments/indexing/vector.py`, `components/reporting/artifacts/vector.py`; `tests/test_vector_workflow.py` |
| Общий site config, launcher, index/search/batch wrappers и notebook | `configs/vector.yaml`, `configs/vector`, `configs/experiments/vacancy-search/vector`, `src/integrations/embedding/launch.py`; `tests/test_vector_launch.py`, `tests/test_vector_configuration.py` |

Итоговый `.venv/bin/pytest -q` после повторного ревью — **874 passed** (198.99 s).
Отдельный прогон пяти vector suites — **326 passed** (33.77 s). `.venv/bin/ruff check .`,
`.venv/bin/ruff format --check .`, `.venv/bin/ty check` и `git diff --check` прошли.
Poetry отсутствует в PATH; использовано существующее project environment
Python 3.12.3. Установка dependencies и lock workflow не повторялись:
manifest/lockfile не менялись.

| Критерии 008 | Исполненное локальное evidence |
| --- | --- |
| L02–L03, L05–L07 | Pure identity/text/JSON и настоящие TEI HTTP fixtures в `test_vector_encoding.py`; captured prefixes/body budgets, malformed batch без downstream calls |
| L04, L06, L12–L16 | `test_vector_workflow.py`: весь corpus до HTTP, UUID collision, golden corpus digest, count/full read-back 8+2, четыре отказа во второй порции, все smoke варианты, ready ambiguity и receipt failure |
| L08–L11, L18 | `test_vector_qdrant.py`: scalar AND/native protocol, descriptor/runtime pins, malformed last hit без mutation и ручной retry, pure immutable plan |
| L01, L17 | Hydra → HTTP index/search/batch → JSON/CSV и subprocess CLI в `test_vector_workflow.py`; site imports и strict vector/retrieval config в `test_vector_configuration.py` |
| L19–L20 | `test_vector_launch.py`: subprocess doubles + настоящий TEI HTTP, argv/environment, cache/name/port/image и deadline failures без cleanup; TEI/Qdrant bounded transport и tokenize budget |
| L21 | Общие gates и все прежние tests; vector notebook дважды исполнен code-cell runner с локальным HTTP, build/audit/preview/search/batch/exports |

Два независимых Astra review по Standards/architecture и specification закрыты;
третий acceptance audit усилил L04/L06/L14. Найденное игнорирование
`qdrant.vector.size/distance` исправлено: обязательная materialized declaration
проверяется владельцем до HTTP, включая null/non-object cases. Отчёт —
[review 008](reviews/2026-10-10-vector-search.md).

Повторное ревью опубликованной поставки `e15aa3b...d608c8f` выявило ещё два P2:
late readiness response и проверку полного query JSON body после Qdrant admission.
Оба исправлены и независимо проверены Astra reviewers. Query body budget теперь
проверяется в pure prepare до любого HTTP. Общий `integrations/http_response`
прерывает чтение body/chunk framing по абсолютному deadline; Timer отменяется
и завершается до возврата. Index experiment не публикует ready после позднего green.
Девять новых tests покрывают точную byte boundary/escaping/zero HTTP, slow body/
chunked framing и отсутствие PATCH/export при late health. DNS/initial headers
сохраняют ограничения urllib/socket; current contract уточнён в architecture/spec.
Повторно проверены 69 локальных Markdown targets/anchors в четырёх изменённых docs
и `git diff --check`. Dependencies, Hydra defaults и public export formats не менялись.

Tests подтверждают pure и локальное HTTP поведение. Реальные tokenizer lengths,
GPU numerical parity, Qdrant filtering/HNSW, persistence, качество и performance
ими не подтверждаются. Реальный Jupyter UI не запускался. На целевой машине нужны
подтверждённые GPU/driver/image, user-owned Qdrant и representative corpus для G01–G06.

Текущие owner contracts находятся в [архитектуре](architecture.md), термины — в
[CONTEXT](../CONTEXT.md), команды общего site workflow — в [README](../README.md).
Qdrant process/storage остаётся user-owned. Приложение строит новые поколения и
допускает к поиску только совместимые ready collections; ошибка receipt export
не отменяет подтверждённую удалённую готовность. OpenSearch default и прежние
форматы search/batch сохраняются.

При финализации спецификации три GPT-6 Astra субагента проверили infrastructure,
encoding и Qdrant contracts. Проверены 46 локальных Markdown targets и синтаксис 22 JSON/YAML/
shell/Python примеров в spec и research notes, whitespace и `git diff --check`.
Отдельная read-only Hydra composition через `--cfg job` подтвердила общий site
config для launcher/index/interactive/batch; эксперименты и сервисы не запускались.
При обновлении current docs проверены 105 локальных targets/anchors, 14 JSON/YAML
примеров и повторно собраны все четыре CLI-команды общего site workflow с `--cfg job`.

## Поставка 007: исправления research evidence

[Spec 007](specs/007-research-evidence-corrections.md) реализована локально 2026-10-03.
Закрыты обе группы замечаний [аудита переноса](reviews/2026-10-03-transfer-readiness.md):
R1 — вложенные source paths с приоритетом точного ключа, R2 — диагностика numeric
epoch milliseconds, R3 — тип score sort key, R4 — различение missing и invalid took.
Владельцы — `RankingConfig.source_diagnostics` и `observe`; query/Painless,
конфигурация, зависимости и форматы экспорта не изменились. Точные текущие правила
закреплены в [архитектуре](architecture.md).

Все перечисленные проверки находятся в
[tests/test_research_ranking.py](../tests/test_research_ranking.py):

| Критерии 007 | Evidence |
| --- | --- |
| A1–A2: paths, приоритет literal key, null, snapshots, отсутствие coercion | `test_source_diagnostics_resolve_paths`, `test_source_diagnostics_snapshot_nested_values` |
| A3–A4: даты, fixed time, timezone и bool | `test_source_dates_preserve_representation_and_fixed_time`, `test_boolean_source_date_is_not_epoch_one` |
| A5: активные поля и совместимость C1/C4/C5 | `test_nested_metadata_http_export_preserves_request_and_source` для balanced/freshness-only/quality-only; прежний `test_text_candidates_do_not_require_ranking_metadata` |
| A6: finite/null score и malformed values | `test_research_sort_score_preserves_numbers_and_null`, `test_research_sort_score_rejects_invalid_values`, `test_nonfinite_sort_score_fails_at_http_boundary` |
| A7: took и пустая выдача | `test_research_timing_rejects_present_invalid_values`, `test_research_timing_and_empty_sorted_results` |
| A8, A11: атомарный отказ и повторный ручной поиск | `test_malformed_evidence_preserves_payload_and_allows_retry` для sort/took |
| A9–A10: остановка batch, отсутствие вызова exporter и сохранность файла | `test_malformed_second_batch_response_stops_without_export` для sort/took и нового/существующего пути |
| A12: фактический HTTP body, filters, params, полные fields и research JSON | `test_nested_metadata_http_export_preserves_request_and_source`, прежний составной тест всех кандидатов |

До исправлений адресный прогон воспроизвёл R1–R4: **30 failed, 26 passed**.
После исправлений `.venv/bin/pytest -q tests/test_research_ranking.py` —
**101 passed**; полный `.venv/bin/pytest -q` — **548 passed** (162.05 s).
Прошли `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .` и
`.venv/bin/ty check`. Проверены локальные Markdown targets, отсутствие outputs
в обоих notebooks и whitespace diff. Новый код не содержит временной instrumentation.

Poetry отсутствует в PATH: installation/`poetry check --lock` не повторялись,
использовано существующее project environment, как разрешает spec 006.
Notebook cells и CLI проверены полным pytest с локальным HTTP; реальный Jupyter UI,
OpenSearch/Painless и целевое подключение не проверены. Внешний план переноса
сохранён в разделе 11 spec 007; его статус **не выполнен**, что не меняет
согласованную границу локальной поставки.

## Поставка 006: русскоязычные рабочие профессии

[Spec 006](specs/006-query-and-painless-candidates.md) и обязательное
[приложение 006A](specs/006-blue-collar-content.md) реализованы локально:
C1 — текстовый контроль, C2 — ограниченные Painless-бонусы и rescore-абляция,
C3 — строгий порядок графиков, C4 — concept/slot Query Understanding,
C5 — консервативное исправление опечаток, раскладки и curated forms.
Все перечисленные абляции собираются через Hydra; полные scripts/params лежат в YAML.

Первый профиль содержит 64 профессии, 321 форму распознавания ролей, 40 навыков,
17 квалификаций, 20 городов, 40 направленных исправлений, 249 независимых query cases,
96 вымышленных вакансий и 32 контрастные группы. Notebook, одиночный и пакетный CLI,
preview и отдельный research JSON поставлены вместе с контентом. Команды, настройка
полей и коэффициентов — в [README](../README.md).

| Обязательство | Owner и evidence |
| --- | --- |
| C1–C5 и все абляции | `configs/jobboard/lexical/research`, `ranking`; `tests/test_research_ranking.py` |
| Parser, routing, rewrite и защита соседних профессий | `src/components/query/jobboard`, `src/core/transformers/opensearch/jobboard`; `tests/test_work_understanding.py` |
| Наполненный русский контент и ожидаемые outcomes | `configs/jobboard/lexical/lexicons/blue-collar`, `cases/blue-collar`; `tests/test_blue_collar_content.py` |
| Preview равен отправленному DSL; preflight и отсутствие partial export | `ResearchExperiment`, `ResearchSearchTransformer`; `tests/test_research_ranking.py` |
| Отдельные sort keys, source metadata, trace, timing и snapshots | `ObservedSERP`, `ResearchArtifact`; `tests/test_research_ranking.py` |
| CLI compare/batch, notebook дважды, JSON и прежний CSV | `tests/test_research_ranking.py` с локальным HTTP и subprocess |
| Совместимость прежних экспериментов | Полный pytest, включая suites specs 001–005 |

Source template требует только site-specific inputs: URL/auth, index, реальные поля,
типы/значения и время сессии. C1/C4/C5 не требуют ranking metadata; single-signal C2
проверяет только активное поле. Source declarations не заменяют проверку mapping.
Метаданные аномалий помечены как локальная проверка возвращённого `_source`,
не чтение doc values и не объяснение вычислений OpenSearch.

OpenSearch не устанавливался, Painless не исполнялся, качество и latency не измерялись.
Эта граница прямо задана пользователем; локальный HTTP не подтверждает удалённую
совместимость. Исследовательские основания сохранены в заметках о
[Painless](research/2026-09-29-painless-ranking.md) и
[Query Understanding](research/2026-09-29-query-understanding.md).

## Реализовано

[Spec 001](specs/001-interactive-search.md) реализована по явному запросу пользователя:
домен, PipelineData, registry, transformers, OpenSearch integration, CLI и JSON-экспорт.
Исходные файлы и абстракции сохранены; добавлены недостающие реализации в тех же
областях. Настройка запуска описана в [README](../README.md).

Python 3.12+, зависимости и инструменты определены в `pyproject.toml`,
воспроизводимое разрешение — в `poetry.lock`. Runtime использует Hydra и standard library;
отдельный OpenSearch SDK не требуется.

По запросу наполнения оставшегося каркаса добавлены возможности
[spec 002](specs/002-evaluation-and-composition.md): NDCG по явной разметке,
описательная статистика, извлечение score, top-k, последовательные независимые
ветки, RRF и эксперимент сравнения. Интерактивный поиск по-прежнему не требует
метрик. Backend оставлен пустым резервом по явному указанию пользователя;
стандартный OpenSearch полностью принадлежит `integrations/opensearch`.

## Отдельный jobboard-эксперимент

[Spec 004](specs/004-end-to-end-search.md) и
[контент 004A](specs/004-jobboard-content.md) реализованы локально. Эксперимент
находится в `configs/experiments/vacancy-search/jobboard/lexical/`; supporting groups
— в `configs/jobboard/lexical/`. Общий framework и default experiment сохранены.

Доменные immutable query values принадлежат `domain/request/jobboard.py`,
модульный parser — `components/query`, compiler и JobSearchTransformer —
`core/transformers/opensearch/jobboard`. Существующие интеграция, registry,
ветвление/RRF/TopK, интерактивный поиск и сравнение переиспользованы без изменения
их public contracts. Runtime dependencies не добавлены.

Поставлены baseline, lexical, parsed, fused, opt-in fuzzy; абляции no_phrase,
no_aliases, no_topical_gate, strict_coverage, roles_skills и no_entities.
Наполнение включает 73 записи RU/EN jobboard-seed-v1, protected/syntax rules,
20 вымышленных вакансий, 24 queries с полной матрицей 480 judgments, отдельные
cases явных filters и q01/q02 для composed smoke. Реальные source index/fields
обязательны; mapping не выводится из словаря или fixture.

Исследование 2026-09-26 опирается на первичные материалы
[job search](research/2026-09-26-jobboard-search.md) и
[OpenSearch/Lucene](research/2026-09-26-opensearch-lexical.md).
Начальные boosts и словарь остаются гипотезами для конкретного индекса.
Локальная реализация не доказывает преимущество retrieval или совместимость
анализатора реального источника. Для этого нужны внешние mapping/версия и judges.

## Evidence

2026-09-29 перед commit/merge spec 006 полный прогон завершился **474 passed**.
Прошли Ruff check, Ruff format --check (121 файл), ty check из существующего `.venv`,
проверка 134 локальных Markdown-ссылок и whitespace новых/изменённых файлов.
Notebook сохранён без outputs; все его code cells дважды исполнены с локальным HTTP,
JSON и прежним CSV-экспортом. Одиночный и пакетный CLI проверены через subprocess.
Примеры research overrides в README отдельно compose/instantiate и preview без HTTP;
исправлен обязательный `+` у override `concept_coverage`.
Poetry отсутствует в PATH; установка и lock verification не повторялись.
Manifest и lockfile не менялись. OpenSearch/Painless не запускались.

2026-09-27 перед фиксацией spec 005 повторно прошли **185 tests**, Ruff check,
Ruff format --check (101 файл) и ty check из существующего `.venv`.
Проверены 74 локальные Markdown-ссылки и отсутствие сохранённых outputs notebook.
Poetry по-прежнему отсутствует в PATH; installation/lock verification и live
OpenSearch smoke не выполнялись. Зависимости и lockfile не менялись.

2026-09-26 реализована [spec 005](specs/005-search-research.md): одиночный
`SearchExperiment.search`, просмотр DSL через `build_query`, исполняемый notebook,
BatchSearchExperiment с CSV-входом и атомарная CSV-выгрузка полных выдач.
Basic и jobboard batch wrappers переиспользуют существующие конфиги поиска.
Входной набор не требует judgments; новое CSV-представление не меняет прежние
searches.json/comparison.json. JSON и CSV используют общий механизм публикации.

Прошли **185 tests**, Ruff check, Ruff format --check (101 файл), ty check из
существующего `.venv`. Poetry отсутствует в PATH; зависимости и lockfile не
менялись, installation/lock verification в этой волне не выполнялись.
`tests/test_search_research.py` проверяет реальные Hydra/HTTP/CLI paths для
basic/fused batch, повторные ручные вызовы, DSL preview без HTTP, CSV quoting/BOM,
Unicode, null score, пустую выдачу, полные поля, статические context/filters,
проверку всего входа до поиска, остановку по ошибке и отсутствие overwrite.
Все code cells поставляемого notebook дважды исполнены в Python с локальным
HTTP, включая экспорт. Это проверка содержимого ячеек, не запуска Jupyter UI/kernel.
Реальный OpenSearch и качество retrieval остаются непроверенными.

Финальное [ревью 004/004A](reviews/2026-09-26-jobboard-lexical.md) 2026-09-26
закрыло два замечания: локальная проверка fuzzy/MSM settings и привязка fuzzy-защиты
к конкретному вхождению слова. После исправлений прошли **164 tests**, Ruff check,
Ruff format --check, ty check и staged/unstaged diff checks из `.venv`.
Открытых существенных замечаний по Standards и Spec не осталось.

2026-09-26 после реализации 004/004A прошли **150 tests**, Ruff check,
Ruff format --check (92 файла), ty check и `git diff --check` из существующего `.venv`.
После уточнения разметки отдельного city-filter case повторно прошли 4 content tests.
Poetry отсутствует в PATH: использованы `.venv/bin/pytest`, `.venv/bin/ruff` и
`.venv/bin/ty`; новая Poetry installation и lock verification в этой волне не выполнялись.
Manifest и lockfile не менялись. Проверены 65 локальных Markdown-ссылок.

Jobboard subprocess CLI проверен с реальным локальным HTTP для interactive/comparison,
baseline/lexical/parsed/fused и opt-in fuzzy. Для q01/q02 получены заранее заданные
NDCG@2: lexical `0.6309297536`, parsed/fused `1`, population stddev `0`.
Regression depth=3/final_k=2 сохраняет общий третий кандидат j01 с RRF score `2/63`.
Проверены исходящие DSL и полные поля `_source`, оба явных filter-типа, один text-field,
успешная пустая выдача, ошибки budget/parser/capability до HTTP, malformed/partial/500
responses, unknown judgment, отсутствие частичного экспорта и сохранность прежнего
retrieval-файла. Пять вариантов конфигурации из README отдельно instantiate через Hydra.
Эти ranks задаёт fixture; они не рассчитаны BM25 и не доказывают превосходство parsed.

После [завершения каркаса](specs/003-cli-completion.md) 2026-09-20 через Poetry
прошли 95 тестов, Ruff check, Ruff format --check и ty check. Manifest и lock
проверены `poetry check --lock`; версии зависимостей при переходе с uv сохранены.
Чистая установка `poetry install` в отдельное временное окружение и импорт
зависимостей также прошли; окружение и его download cache удалены.
Backend оставлен резервом по указанию пользователя.

Отдельно выполнен настоящий `poetry run python -m src.run` с локальным HTTP:
два запроса, полные поля, prefix URL, Authorization и EOF с `searches.json`;
ошибка второго запроса останавливает сессию до третьего без артефакта.
Команда локального сравнения из README создаёт `comparison.json` без поиска.
Проверены 45 локальных Markdown-ссылок и `git diff --check`.
Poetry 2.5.1 запускался из изолированного tool-окружения, без глобальной установки.

2026-09-21 перед фиксацией текущего состояния повторно прошли 95 тестов,
Ruff check, Ruff format --check и ty check из существующего `.venv`.
Poetry отсутствовал в PATH, поэтому его команды в этой проверке не повторялись.
Сверка lockfiles подтвердила сохранение версий всех 12 внешних зависимостей;
проверены 45 локальных Markdown-ссылок и `git diff --check`.
Дополнительные рабочие каталоги worktree отсутствуют. Сохранившиеся ветки
относятся к исторической архитектуре до нового каркаса; их отдельные коммиты
не означают незавершённую реализацию specs 001–003 и автоматически не переносятся.

| Контракт | Исполняемая проверка |
| --- | --- |
| Mutation, copy, immutable/append-only filters | `tests/test_pipeline.py` |
| Lookup identity, ссылки, циклы, порядок и вложенность | `tests/test_pipeline.py` |
| DSL, ошибки до network calls, отсутствие partial mutation | `tests/test_search.py` |
| POST с prefix URL, auth, timeout, закрытие response, запрет redirect | `tests/test_search.py` |
| Поля, порядок и score; malformed/partial response | `tests/test_search.py` |
| Снимки артефакта, атомарная публикация, отсутствие overwrite | `tests/test_reporting.py` |
| Реальная Hydra-композиция и subprocess CLI с локальным HTTP | `tests/test_cli.py` |
| Два поиска, EOF, exit codes, остановка по ошибке, отсутствие утечки auth | `tests/test_cli.py` |
| Изоляция веток, атомарная публикация, циклы, RRF, extractor и top-k | `tests/test_composition.py` |
| NDCG, статистика, сравнение и Hydra-композиция с экспортом | `tests/test_evaluation.py` |
| Unicode, protected/ambiguous spans, independent stages, syntax и budgets | `tests/test_jobboard_parser.py` |
| Source capabilities, literal/concept/topical/fuzzy DSL, atomic failures | `tests/test_jobboard_search.py` |
| Заполненные lexicons/cases, семантика контента и Hydra ablations | `tests/test_jobboard_content.py` |
| Jobboard subprocess CLI/HTTP, RRF depth, известный NDCG, no partial export | `tests/test_jobboard_workflow.py` |
| Ручной поиск, DSL preview, notebook cells, CSV batch/CLI и атомарная выгрузка | `tests/test_search_research.py` |

Команды проверки — в [AGENTS.md](../AGENTS.md). Тесты проверяют публичные результаты
и существенные failure paths. В сквозном тесте используется настоящий HTTP-клиент,
эксперимент, registry, transformers, преобразование документов и exporter; сервер
возвращает контролируемый стандартный OpenSearch response.

## Проверка всего src

Проверены все Python-модули, включая package-файлы и наследуемое поведение.
Пустые package-файлы получили описание области; классы-маркеры — описание
контракта без дублирования наследуемой реализации.

| Область | Реализация и свидетельство |
| --- | --- |
| `domain/document`, `domain/collection` | Dataclasses с независимыми containers; Vacancy и SERP специализируют их. Поля, score, порядок и копии проверяются pipeline/search tests. |
| `domain/request`, `domain/__init__.py` | JSON-типы, frozen filters и snapshot Request; проверены immutable параметры и независимость запросов. |
| `core/registry` | Lookup готовых объектов и проверка графа pipelines/веток; проверены identity, неизвестные ссылки и циклы. |
| `core/transformers` | Payload, последовательность, ветки, top-k, extractor, RRF и поисковый DSL; проверены успешная композиция, изоляция и ошибки до публикации. |
| `integrations/opensearch` | HTTP search, владение внешним hit и преобразование в Vacancy/SERP; локальный HTTP и malformed/failure tests. |
| `components/evaluation` | NDCG и описательная статистика; известные значения, cutoff, ошибки разметки и числовые границы. |
| `components/reporting` | Снимки артефактов и атомарный JSON-export; независимость данных, отсутствие overwrite и partial writes. |
| `experiments`, `run.py` | Интерактивный поиск и сравнение, Hydra, экспорт только после успеха, exit codes; оба сценария запущены через Poetry. Незавершённая SERP отклоняется до вывода и экспорта. |

Абстрактные методы задают обязательные операции конкретных реализаций и
намеренно остаются абстрактными. `BaseIntegration`, `BaseRequest`, `BaseFilter`
и специализированный registry обозначают границы типов. Единственный резерв
без реализации — `integrations/backend`; его протокол не придуман.

## Ограничение среды

На текущем устройстве нет развёрнутого OpenSearch и сетевого доступа к реальному
прокси. По явному условию пользователя live smoke не является блокером локальной
поставки. Он **не выполнен**: локальный HTTP-тест не подтверждает конкретный
удалённый endpoint, credentials, mapping или доступность индекса.

Перед первым использованием на устройстве с доступом достаточно выполнить
[инструкцию запуска](../README.md), сделать запрос и завершить сессию через EOF.
Проверь отображённую выдачу и `searches.json`. До этого внешняя совместимость
конкретного подключения не объявляется проверенной.

## Выбранные детали реализации

Фильтры используют frozen dataclasses со scalar-параметрами; PipelineData раскрывает
их immutable tuple только для чтения. Общий BaseRegistry специализируется через
наследование, registry явно передаётся в transform. Это сохраняет исходные
абстракции без DI/bind framework и второго механизма композиции.

Экспорт публикует временный файл через atomic hard link в том же каталоге.
Существующий результат сохраняется даже при конкурентной публикации; транзакций
между несколькими exporters нет. Ошибка запроса прекращает всю сессию без
сохранения частичного артефакта.

Секрет передаётся через окружение и Authorization header. Сохранённый конфиг Hydra
содержит интерполяцию, а не разрешённое значение. Верхняя граница запуска выводит
безопасные фазу и тип ошибки; сырые исключения транспорта и конфигурации могут
содержать секреты и не печатаются.
