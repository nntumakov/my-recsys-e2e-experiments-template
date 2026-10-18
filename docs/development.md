# Текущий статус

Проект запускается из checkout на Python 3.12+; состав объектов и параметры
задаются Hydra. Установка и команды проверок — в [CONTRIBUTING](../CONTRIBUTING.md).

## Что работает

| Сценарий | Вход и результат |
| --- | --- |
| Локальное сравнение ranking | Заданные кандидаты и judgments → text/preference/RRF, NDCG, comparison.json |
| Базовый OpenSearch | Текст и явные AND filters → полная SERP; EOF сохраняет searches.json |
| Jobboard lexical | RU/EN parser, aliases, coverage, phrase, fuzzy и RRF |
| Blue-collar research | Русские роли/условия/квалификации, bounded Painless, tiers и conservative rewrite |
| Notebook и batch | Ручной поиск/preview, CSV-набор запросов, полные выгрузки |
| Векторный поиск | JSON-корпус, TEI/E5, проверяемые поколения Qdrant, search и batch |
| Search study | Пять fulltext и пять embedding политик, preview, карточки, matrix рангов и snapshots |

Для начала достаточно [Python-only примера](examples/offline-ranking.md).
Внешние сценарии требуют своих endpoint, mapping и данных:
[руководство](usage.md). Реализация конкретной возможности и её локальная проверка
не означают подтверждённого качества поиска.

## Подготовка публичного проекта

Добавлен конфиг `ranking` и Python Compose API example на существующем
CompareExperiment. Три запроса, по пять кандидатов, три pipelines и полная
независимая разметка дают девять результатов. Единственный новый шаг обработки —
ScoreSortTransformer: стабильный descending order конечных scores с полной
проверкой перед заменой коллекции. Ветви RRF получают полную глубину до TopK.
Контракт — [OpenSpec offline-ranking](../openspec/specs/offline-ranking/spec.md),
владельцы — в [архитектуре](architecture.md).

README ведёт к исполняемому примеру; подробные внешние команды вынесены в usage.
OpenSpec настроен для новых изменений; действующие specs 001–009 сохраняются.
Добавлена MIT. Для публикации подготовлена отдельная история из трёх тематических
коммитов; исходные 140 коммитов сохранены в локальной `codex/internal-history`.
Это новые снимки текущего проекта, не реконструкция дат или порядка разработки.
Публикация remote не выполнялась; master не менялся.

## Проверки текущего изменения

Адресный прогон ranking/composition/evaluation suites: **33 passed**.
Он проверяет stable ties, отсутствие
mutation при неверном последнем score, повторяемость без сети, полный RRF depth,
изменение Hydra parameters, совпадение Python/CLI и отсутствие overwrite/partial export.

Полный `pytest -q`: **1025 passed** (551.05 s). Ruff check, Ruff format и ty check
проходят в существующем окружении Python 3.12.3. Исполнены Python-блок walkthrough,
module entry point и четыре CLI-конфигурации; их артефакты совпадают с Compose API.
Проверены локальные ссылки и anchors, OpenSpec strict validation и whitespace
всего подготовленного дерева.
Poetry отсутствует в PATH: используется существующее project environment через
временную `.venv` ссылку, удалённую после проверок. Установка зависимостей и lock verification
в этом изменении не повторялись; manifest и lockfile не менялись.

## Границы проверки интеграций

HTTP fixtures исполняют настоящий клиент, Hydra composition и обработку ответов.
Они не исполняют BM25, Painless, GPU inference или native Qdrant ranking.
OpenSearch/Painless и live GPU/Qdrant gates в этой среде не выполнялись.
См. [008 G01–G06](specs/008-vector-search.md) и
[009 G01–G09](specs/009-search-experiment-matrix.md). Jupyter frontend требует
отдельной проверки; тесты notebook cells не заменяют работу в UI.

Векторные поколения immutable после публикации. Процесс и storage Qdrant,
TEI cache и адресное восстановление после remote failure принадлежат оператору.
Backend integration оставлена резервом; протокол и реализация не добавлены.

Подробные результаты прежних поставок и review сохранены в
[истории evidence](history/delivery-evidence.md). Там указаны выполненные на тот
момент проверки и ограничения; они не выдаются за новый прогон публичного checkout.
