# Работа над recsys-experiments

Эксперименты пишутся на Python и собираются Hydra. Сохраняй структуру каталогов,
типизированные компоненты и композицию transformers через имена в registry.

## Перед изменениями

- При первом знакомстве прочитай [архитектуру](docs/architecture.md) целиком.
  Для данных, registry, transformers и интеграций сверяйся с её инвариантами.
- При изменении доменных типов или терминов прочитай [CONTEXT.md](CONTEXT.md).
- Перед выбором проверок прочитай [текущий статус](docs/development.md).
- Для ranking, Query Understanding и русского контента читай
  [006](docs/specs/006-query-and-painless-candidates.md) и
  [006A](docs/specs/006-blue-collar-content.md); для notebook/batch —
  [005](docs/specs/005-search-research.md).
- Изменения нескольких компонентов, публичного контракта или persistent data
  описывай в OpenSpec: [порядок работы](CONTRIBUTING.md#openspec),
  [контекст проекта](openspec/config.yaml). Укажи результат, границы, ошибки
  и проверку приёмки до реализации. Specs 001–009 сохраняют действующие контракты.

## Правила

- Весь Python, включая примеры, размещай в `src/`, конфигурацию — в `configs/`.
- Используй Hydra composition и интерполяции для связей. Конфиги группируй по задаче
  и варианту, как в `configs/experiments/vacancy-ranking/local/`.
- Сохраняй владельцев состояния и направление зависимостей. Существенную замену
  абстракций согласуй до реализации.
- Комментарии объясняют ограничения и причины решений. Подробные форматы принадлежат
  specs, текущее устройство — architecture, результаты проверок — development.
- Обновляй код и затронутый контракт вместе. Проверяй внешние границы, избегая
  повторной валидации одного инварианта на каждом шаге pipeline.

## Проверки

Python 3.12+, Poetry 2.2+; зависимости и настройки инструментов — в pyproject.toml.

```sh
poetry install
poetry run ruff check .
poetry run ruff format --check .
poetry run ty check
poetry run pytest
```

Тесты с HTTP fixtures не подтверждают работу реальных OpenSearch/Painless,
Qdrant или TEI. Для документации проверяй локальные ссылки и `git diff --check`.
Python-only пример — в [README](README.md), внешние подключения — в
[usage](docs/usage.md), публикационная ветка — в [CONTRIBUTING](CONTRIBUTING.md#публикационная-ветка).
