# recsys-experiments

Эксперименты с поиском и ранжированием вакансий: сравнение выдач, разбор запросов
и оценка качества на размеченных данных.

Сценарии написаны на Python, состав обработки задаётся Hydra. Именованные
transformers работают с общим PipelineData; ветки исполняются на независимых копиях,
результаты сохраняются отдельными артефактами. Подключены OpenSearch и TEI/Qdrant.

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

Команда печатает JSON без записи на диск. [Python-пример](src/experiments/examples/ranking.py)
собирает CompareExperiment через Compose API. Параметры меняются Hydra overrides:

```sh
poetry run python -m src.run --config-name ranking ranking.k=2 ranking.rank_constant=0
```

## Что посмотреть дальше

| Задача | Где начать |
| --- | --- |
| Разобрать конфигурацию, ветки, RRF и NDCG | [Локальный пример](docs/examples/offline-ranking.md) |
| Подключить свой индекс или запустить batch | [Руководство по внешним сервисам](docs/usage.md) |
| Сравнить fulltext и embedding варианты в notebook | [Search study](src/experiments/notebooks/search-study.ipynb) и [подготовка](docs/usage.md#сравнение-десяти-вариантов) |
| Понять границы компонентов | [Архитектура](docs/architecture.md) и [словарь](CONTEXT.md) |
| Изменить проект, запустить проверки, работать с OpenSpec | [CONTRIBUTING](CONTRIBUTING.md) |
| Узнать, что проверено и что требует реального сервиса | [Текущий статус](docs/development.md) |

Код находится в `src/`, конфиги — в `configs/`, тесты — в `tests/`.
Учебные данные показывают механику эксперимента. Качество реального поиска
требует своего корпуса и независимой разметки.

Лицензия — [MIT](LICENSE).
