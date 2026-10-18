# offline-ranking Specification

## Purpose

Воспроизводимое сравнение ранжирования заданных вакансий через существующие
Hydra, registry и transformers без внешних сервисов. Сценарий дополняет
[композицию и оценку из spec 002](../../../docs/specs/002-evaluation-and-composition.md).

## Requirements

### Requirement: Python-only composed comparison
Проект SHALL предоставлять локальное сравнение text, preference и RRF pipelines
на трёх вымышленных наборах кандидатов с полной явной разметкой. Сценарий SHALL
собираться Hydra через существующий CompareExperiment и типизированные transformers,
не выполнять сетевые запросы и не требовать новых зависимостей.

#### Scenario: Run from a checkout
- **WHEN** пользователь запускает документированную CLI-команду или Python Compose API
- **THEN** возвращаются девять результатов case/pipeline, полные поля документов,
  NDCG@k и описательная статистика; повторный запуск даёт тот же результат

#### Scenario: Independent judgments
- **WHEN** пользователь меняет числовой признак ранжирования
- **THEN** оценки релевантности остаются независимой разметкой, а не выводятся из score

### Requirement: Stable score ordering
ScoreSortTransformer SHALL сортировать документы по конечному числовому score
по убыванию, сохраняя исходный порядок при равенстве и конкретный тип коллекции.
Проверка всей коллекции SHALL предшествовать её замене; fields, filters, context
и branch workspace SHALL сохраняться.

#### Scenario: Ties and repeated execution
- **WHEN** два документа имеют одинаковый score
- **THEN** их взаимный порядок сохраняется; повторная сортировка не меняет результат

#### Scenario: Invalid final candidate
- **WHEN** последний документ содержит отсутствующий, bool или неконечный score
- **THEN** transformer отклоняет весь вызов без перестановки или изменения документов

### Requirement: Complete publication
Пример SHALL использовать существующий ComparisonArtifact и atomic no-overwrite
JSONExporter; ошибка метрики SHALL исключать публикацию частичного результата.

#### Scenario: Existing destination
- **WHEN** экспорт направлен в уже существующий файл
- **THEN** экспорт завершается ошибкой, сохраняя прежнее содержимое

### Requirement: Explainable onboarding
Документация SHALL объяснять признаки, judgments, ветвление до ограничения top-k,
RRF, чтение per-case результатов и ограничения синтетического примера.

#### Scenario: Follow the walkthrough
- **WHEN** читатель выполняет README и Python-пример
- **THEN** он может найти конфиг, поменять k или rank_constant и получить новый артефакт
  без изменения реализации, сервисов или глобального окружения
