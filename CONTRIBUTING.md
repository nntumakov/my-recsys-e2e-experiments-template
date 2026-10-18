# Разработка

Нужны Python 3.12+ и Poetry 2.2+. После `poetry install`:

```sh
poetry run ruff check .
poetry run ruff format --check .
poetry run ty check
poetry run pytest
```

Тесты поднимают временные HTTP-серверы на loopback; внешние сервисы и ключи
не нужны. Проверка настоящего OpenSearch или GPU выполняется отдельно.
Границы проверенной совместимости перечислены в [development](docs/development.md).

Весь Python, включая код примеров, живёт в `src/`. Конфигурация компонентов —
в `configs/`: Hydra создаёт объекты, registry связывает transformers по именам.
Начать знакомство можно с [локального сравнения](configs/experiments/vacancy-ranking/local/001-comparison.yaml),
затем прочитать [архитектуру](docs/architecture.md) и [словарь](CONTEXT.md).

## OpenSpec

Для новых изменений нескольких компонентов используем
[OpenSpec](https://github.com/Fission-AI/OpenSpec). Для небольшого локального
исправления достаточно кода, нужной проверки и обновления затронутого контракта.
OpenSpec нужен разработчику, но не нужен для запуска экспериментов.

В проекте настроен CLI 1.14.0. Если его нет, установи изолированно, без глобальной
установки и изменения Python lockfile:

```sh
npm exec --yes --package=@fission-ai/openspec@1.14.0 -- openspec --version
```

Следующие команды предполагают доступный `openspec`; с `npm exec` используй
тот же префикс перед каждой командой. Повторный `init` после клонирования не нужен.

```sh
openspec list
openspec new change short-change-name
openspec status --change short-change-name
openspec instructions proposal --change short-change-name
```

В `openspec/changes/<name>/` последовательно появляются `proposal.md`,
`specs/<capability>/spec.md`, `design.md` и `tasks.md`. Spec описывает поведение,
совместимость, ошибки и evidence приёмки; design — владельцев и решения;
tasks — исполнимую работу. Перед реализацией существенные открытые решения
должны быть согласованы. Явный запрос на реализацию делегирует тактические решения
внутри принятого scope.

После реализации выполни gates, отметь проверенные tasks и проверь change:

```sh
openspec validate short-change-name --strict --no-interactive
openspec archive short-change-name --yes
openspec validate --all --strict --no-interactive
```

Archive переносит change в `openspec/changes/archive/` и обновляет текущие
requirements в `openspec/specs/`. Файлы в `.agents/skills/openspec-*` сгенерированы
CLI для Codex; в новом сеансе они доступны как навыки OpenSpec. Обновляй их через
`openspec update`, проверяя diff.

Действующие specs 001–009 остаются в `docs/specs/`: массовый перенос ради формата
не нужен. Новая OpenSpec spec ссылается на затронутый старый контракт и явно
описывает изменение. Термины обновляются в `CONTEXT.md`, технические контракты —
в `docs/architecture.md`, статус и реальные проверки — в `docs/development.md`.

## Изменения

В описании изменения укажи наблюдаемое поведение, причину и выполненные проверки.
Для интеграций различай HTTP fixtures и реальный сервис. Коммить код вместе
с относящимися к нему конфигурацией, тестами и документацией.

Проект распространяется под [MIT](LICENSE).
