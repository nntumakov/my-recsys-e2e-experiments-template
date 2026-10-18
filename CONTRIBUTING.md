# Разработка

Нужны Python 3.12+ и Poetry 2.2+. После `poetry install`:

```sh
poetry run ruff check .
poetry run ruff format --check .
poetry run ty check
poetry run pytest
```

Тесты интеграций поднимают временные HTTP-серверы на loopback. Проверки настоящих
OpenSearch, GPU и Qdrant описаны отдельно в [development](docs/development.md).

Весь Python, включая примеры, находится в `src/`. Hydra-конфиги — в `configs/`.
Начни с [локального сравнения](docs/examples/offline-ranking.md), затем прочитай
[архитектуру](docs/architecture.md) и [словарь](CONTEXT.md).

## OpenSpec

Изменения нескольких компонентов и публичных контрактов описываем в
[OpenSpec](https://github.com/Fission-AI/OpenSpec). Локальному исправлению
достаточно кода, соответствующей проверки и обновлённой документации.
Для запуска экспериментов OpenSpec не нужен.

Проект настроен на CLI 1.14.0. Его можно вызвать без глобальной установки:

```sh
npm exec --yes --package=@fission-ai/openspec@1.14.0 -- openspec --version
```

Далее используется доступный `openspec`; при запуске через npm exec сохраняй
тот же префикс. Повторный init после клонирования не требуется.

```sh
openspec new change short-change-name
openspec status --change short-change-name
openspec instructions proposal --change short-change-name
```

В `openspec/changes/<name>/` опиши причину и границы в proposal, поведение
и ошибки в specs, решения в design, исполнение и проверки в tasks.
После реализации и проверок:

```sh
openspec validate short-change-name --strict --no-interactive
openspec archive short-change-name --yes
openspec validate --all --strict --no-interactive
```

Archive обновляет `openspec/specs/` и сохраняет change в `openspec/changes/archive/`.
Specs 001–009 остаются действующими контрактами; новые изменения ссылаются на них.
Термины принадлежат CONTEXT, технические объяснения — architecture, состояние
проверок — development. Сгенерированные Codex skills обновляются `openspec update`.

## Публикационная ветка

`codex/public-release` содержит отдельную публичную историю. Исходная разработка
сохранена в `master` и `codex/internal-history`. `origin` — GitHub-репозиторий проекта;
публикационная ветка обновляется под собственным именем, отдельно от master.

Работай в `codex/public-release`. Изменение из внутренней истории переноси выбранным
коммитом через `git cherry-pick <commit>`, затем проверяй diff и запускай gates выше.
Независимые корни веток не требуют merge для публикации.

Первая публикация ветки:

```sh
git switch codex/public-release
git log --oneline -4
git status --short
git push --set-upstream origin codex/public-release
```

Перед следующим обновлением забери remote refs и проверь расхождение:

```sh
git fetch origin
git log --left-right --oneline HEAD...origin/codex/public-release
git diff origin/codex/public-release..HEAD
git push origin HEAD:refs/heads/codex/public-release
```

Если на remote есть чужие коммиты, сначала согласуй и включи их в ветку.
Обычный push отклонит расхождение; автоматический force-push здесь не используется.

Код, конфиги, тесты и относящиеся к ним документы коммить вместе.
Проект распространяется под [MIT](LICENSE).
