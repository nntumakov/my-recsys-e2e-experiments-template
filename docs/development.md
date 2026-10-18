# Состояние проекта

Проект работает из checkout на Python 3.12+; Hydra собирает эксперименты
и их зависимости. Установка и проверки — в [CONTRIBUTING](../CONTRIBUTING.md).

## Возможности

| Сценарий | Вход и результат |
| --- | --- |
| Локальное ранжирование | Кандидаты и judgments → text/preference/RRF, NDCG, comparison.json |
| OpenSearch | Текст и AND-фильтры → полная SERP, searches.json |
| Jobboard lexical | RU/EN parser, aliases, coverage, phrase, fuzzy и RRF |
| Рабочие профессии | Русские роли и условия, bounded Painless, tiers и conservative rewrite |
| Notebook и batch | Preview, ручное сравнение, CSV-запросы и полные выгрузки |
| Векторный поиск | JSON-корпус, TEI/E5, проверяемые поколения Qdrant, search и batch |
| Search study | Пять fulltext и пять embedding-политик, карточки, матрица рангов и snapshots |

Начать можно с [Python-примера](examples/offline-ranking.md). Он использует
CompareExperiment, registry, PipelineData и обычные Hydra-конфиги. Контракт —
[offline-ranking](../openspec/specs/offline-ranking/spec.md), устройство компонентов —
[architecture](architecture.md). Внешние подключения описаны в [usage](usage.md).

Новые изменения ведутся через OpenSpec; specs 001–009 сохраняют действующие
контракты. Проект распространяется под MIT. Порядок обновления отдельной публичной
ветки описан в [CONTRIBUTING](../CONTRIBUTING.md#публикационная-ветка).

## Проверки

Полный регрессионный прогон на `ae8ab96` от 2026-10-18: **1025 passed** за 551.05 s.
Ruff check, Ruff format и ty check прошли. Проверены Python walkthrough, module
entry point и CLI с параметрами k/RRF; результаты совпали с Compose API.

Последующая редактура меняет документацию, docstrings и комментарии. Сверка
подтвердила сохранность всех путей, исполняемого AST 153 Python-файлов без docstrings,
значений 105 YAML-конфигов и кода, metadata и outputs четырёх notebook.
Все 20 блоков команд usage сохранены. Повторно выполнены Ruff, format, ty,
ranking/composition/evaluation и notebook tests (**40 passed**, 107.83 s),
Python walkthrough и четыре CLI-конфигурации. Локальные ссылки, OpenSpec
и whitespace проверены отдельно.

Проверки выполнены в существующем окружении Python 3.12.3. Poetry отсутствует
в PATH; установка из lockfile в этом прогоне не повторялась. Manifest и lockfile
не менялись. Предыдущая чистая установка описана в
[истории проверок](history/delivery-evidence.md).

## Границы проверки

HTTP fixtures проверяют клиент, Hydra-композицию, запросы и обработку ответов.
BM25, Painless, GPU inference и native Qdrant ranking требуют реальных сервисов.
OpenSearch/Painless и live GPU/Qdrant gates здесь не выполнены:
[008 G01–G06](specs/008-vector-search.md),
[009 G01–G09](specs/009-search-experiment-matrix.md).
Notebook cells исполнялись с локальным HTTP; Jupyter frontend требует отдельной
проверки. Учебные выдачи не подтверждают качество поиска на другом корпусе.

Векторные поколения неизменяемы после публикации. Процесс и storage Qdrant,
TEI cache и восстановление после remote failure принадлежат оператору.
Backend integration остаётся резервом без протокола и реализации.

Результаты прежних поставок и review сохранены в
[истории](history/delivery-evidence.md). Даты и ограничения в ней относятся
к соответствующей проверке.
