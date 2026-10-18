# recsys-experiments

Pet-проект для экспериментов с поиском и ранжированием вакансий. Можно собрать
несколько способов обработки запроса, сравнить выдачи и сохранить результат,
не меняя код сценария при каждой настройке весов или состава pipeline.

Эксперименты написаны на Python. Hydra создаёт типизированные компоненты,
registry связывает их по именам, transformers работают с общим PipelineData.
Есть OpenSearch-поиск, разбор русскоязычных запросов рабочих профессий,
RRF, NDCG, notebook для сравнения выдач и векторный поиск через TEI/Qdrant.

## Попробовать без сервисов

Нужны Python 3.12+ и [Poetry 2.2+](https://python-poetry.org/docs/#installation).
Команды выполняются из корня checkout:

```sh
poetry install
poetry run python -m src.run --config-name ranking
```

Пример сравнивает три способа ранжирования: текстовый сигнал, предпочтения по
условиям работы и объединение через RRF. В нём три запроса, по пять вымышленных
вакансий и независимая ручная разметка. Сеть, ключи, Docker и GPU не нужны.

Результат — `outputs/ranking/<дата>/<run>/comparison.json`: полные выдачи,
NDCG@3 по каждому запросу и сводная статистика. На этих данных RRF улучшает
один запрос и ухудшает два других; [разбор примера](docs/examples/offline-ranking.md)
объясняет почему и показывает, как изменить эксперимент.

Та же Hydra-композиция доступна из Python:

```sh
poetry run python -m src.experiments.examples.ranking
```

Команда печатает JSON без записи на диск. В [Python-файле](src/experiments/examples/ranking.py)
нет альтернативного runner: Compose API собирает тот же CompareExperiment.
Параметры CLI остаются обычными Hydra overrides:

```sh
poetry run python -m src.run --config-name ranking ranking.k=2 ranking.rank_constant=0
```

## Что посмотреть дальше

| Задача | Где начать |
| --- | --- |
| Разобрать конфигурацию, ветки, RRF и NDCG | [Локальный пример](docs/examples/offline-ranking.md) |
| Подключить свой индекс или запустить batch | [Руководство по внешним сервисам](docs/usage.md) |
| Сравнить fulltext и embedding варианты в notebook | [Search study](src/experiments/notebooks/search-study.ipynb) и [подготовка](docs/usage.md#десять-вариантов-поиска-и-notebook-сравнения-009) |
| Понять границы компонентов | [Архитектура](docs/architecture.md) и [словарь](CONTEXT.md) |
| Изменить проект, запустить проверки, работать с OpenSpec | [CONTRIBUTING](CONTRIBUTING.md) |
| Узнать, что проверено и что требует реального сервиса | [Текущий статус](docs/development.md) |

Код находится в `src/`, конфиги — в `configs/`, тесты — в `tests/`.
Синтетические данные и HTTP fixtures проверяют механику; они не подтверждают
качество поиска на реальных вакансиях. Live-проверки OpenSearch/Painless,
GPU и Qdrant описаны отдельно в статусе и спецификациях.

Лицензия — [MIT](LICENSE).
