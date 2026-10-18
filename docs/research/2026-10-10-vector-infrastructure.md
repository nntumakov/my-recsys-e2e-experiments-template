# GPU inference и перенос vector search на целевую машину

Дата проверки: **2026-10-10**. Статус: исследование и рекомендация для
[spec 008](../specs/008-vector-search.md), **не выполненное развёртывание**.
Прочитаны [AGENTS.md](../../AGENTS.md), [архитектура](../architecture.md),
[текущая готовность](../development.md), [README](../../README.md),
[manifest](../../pyproject.toml) и текущая реализация [entry point](../../src/run.py).
На этапе исследования код и конфиги vector search не были поставлены. Команды
проектного launcher ниже обозначены как будущие; незавершённые черновики реализации
не являются evidence готовности. Текущий принятый контракт находится в spec 008.

Пользователь поднимает Qdrant самостоятельно. Наш контур отвечает за приложение,
подготовку коллекции через его API и embedding runtime; он не устанавливает,
не запускает и не переконфигурирует сервер Qdrant. Сообщение «NVIDIA t??? или
несколько GPU» недостаточно для выбора образа: **T4 пока не подтверждена**.

## Рекомендация

Приложение запускается из Git checkout через существующие Python 3.12+ и Poetry
2.2+, рядом работает **один Docker-контейнер Hugging Face TEI 1.9.4 на одной
явно выбранной NVIDIA GPU**. Qdrant доступен приложению по внешнему URL из Hydra.
Начальная модель — `intfloat/multilingual-e5-base` на immutable HF revision
`d128750597153bb5987e10b1c3493a34e5a4502a`; mean pooling, 768 измерений,
`query: ` / `passage: `, L2 normalization, максимум 512 tokenizer tokens.
Это воспроизводимый multilingual baseline, а не доказанный лучший encoder
русскоязычных вакансий. Модель и её правила подтверждены первичным model card
и файлами именно этого revision. [M1–M4]

```text
Целевая машина
  Git checkout + Poetry
    ├─ HTTP → 127.0.0.1:<tei_port> → Docker TEI → одна GPU
    └─ HTTP(S) → qdrant.url → Qdrant пользователя

  TEI cache: отдельный постоянный host directory → /data контейнера
  Корпус и артефакты: host filesystem приложения
  Qdrant storage/backups: область ответственности владельца Qdrant
```

Этот выбор оставляет ML/CUDA зависимости в контейнере, сохраняет лёгкий runtime
проекта — Hydra и standard library — и даёт один прогретый encoder для import,
CLI и notebook. Docker Compose, контейнер приложения, новый web backend и
deployment framework для этого пути не требуются. На машине без совместимой
NVIDIA GPU запуск останавливается с объяснением; CPU fallback и другой runtime
автоматически не выбираются.

## Почему TEI, а не локальный Python или vLLM

| Вариант | Проверенный факт | Следствие для первой поставки |
| --- | --- | --- |
| TEI | Поддерживает XLM-RoBERTa с absolute positions; предоставляет token batching, `/embed`, `/info`, `/health`, ограничения запросов. E5-base имеет именно `XLMRobertaModel`, absolute positions. [T2–T6, M2] | Узкая inference-служба соответствует задаче; HTTP-клиент не требует ML dependency. **Рекомендуется.** |
| Sentence Transformers в процессе приложения | `SentenceTransformer` поддерживает `revision`, выбор device, batch encode и normalization; официальный пример E5 использует эту библиотеку. Нужны PyTorch и Transformers. [S1–S2, M1] | Технически корректный и короткий путь для одного Python-процесса. Однако Poetry environment становится GPU-dependent; каждый CLI/kernel получает собственный model lifecycle и потенциально свою копию весов. Для единого inference endpoint это менее подходящий owner. |
| vLLM | Актуальные официальные docs прямо перечисляют `intfloat/multilingual-e5-base` среди XLMRoberta models; есть pooling runner, `/v1/embeddings`, `/pooling`. Нельзя отвергать vLLM как «только generative». [V1–V2] | Дополнительные возможности serving здесь не нужны. Документация отдельно предупреждает о бинарной совместимости CUDA/PyTorch и рекомендует отдельное окружение. Нет измерений, доказывающих преимущество vLLM для этого корпуса/оборудования. [V3] |

Сравнение не является benchmark. Мы не измеряли throughput, latency или качество
любого runtime. При смене TEI на Sentence Transformers нельзя обещать побитовое
совпадение: Candle backend TEI 1.9.4 явно предупреждает, что для `hidden_act=gelu`
использует GeLU+tanh approximation вместо exact GeLU в Transformers. Dtype,
runtime version и image digest нужно сохранять в recipe. [T7]

## GPU: сначала измерить, затем выбрать образ

На **целевой машине** до pull/установки весов выполнить существующие команды:

```sh
df -h /
uname -m
nvidia-smi
nvidia-smi -L
nvidia-smi --query-gpu=index,uuid,name,memory.total,driver_version --format=csv
nvidia-smi --help-query-gpu
docker version
docker info
```

`nvidia-smi` показывает модель, драйвер, память и активные процессы; UUID предпочтительнее
индекса для закрепления GPU. Если установленный `--help-query-gpu` перечисляет
`compute_cap`, запросить также `nvidia-smi --query-gpu=uuid,name,compute_cap --format=csv`;
иначе определить capability по точной модели в официальной таблице NVIDIA.
Проверить, что выбранная GPU не занята другим необходимым workload. Значение
`CUDA Version` / `CUDA UMD Version` в `nvidia-smi` — максимум CUDA, поддержанный
драйвером, а не свидетельство установленного host toolkit. [N1–N2]

TEI docs говорят о CUDA compute capability **не ниже 7.5**; V100, Titan V и GTX
1000 этим CUDA-контуром не поддерживаются. Runtime images архитектурно различаются.
На 2026-10-10 GHCR manifest API реально вернул все patch tags в следующей таблице;
слои образов не скачивались. Tags `1.9` из документации являются подвижными;
для запуска использовать `repository:patch-tag@sha256:…`. [T1–T3, G1]

Все строки используют repository `ghcr.io/huggingface/text-embeddings-inference`.

| Compute capability | Примеры архитектуры | Проверенный tag | OS/architecture | Manifest index digest |
| --- | --- | --- | --- | --- |
| 7.5 | Turing: T4, RTX 20xx | `turing-1.9.4` | `linux/amd64` | `sha256:f78d17832f00f59f7d52e4b3b8495264257de3c18b773357a00ee341b2af852d` |
| 8.0 | Ampere: A100, A30 | `1.9.4` | `linux/amd64` | `sha256:02864b69c15e4f247b8306b0be8801d6800fed97702185fb19d98170e956fab1` |
| 8.6 | Ampere: A10, A40 | `86-1.9.4` | `linux/amd64` | `sha256:bee0bdb887096229add91b6fdfcf95258c12eb9180e90dc86f3b4b25b86fc613` |
| 8.9 | Ada: RTX 40xx, L4 | `89-1.9.4` | `linux/amd64` | `sha256:1a284d9ca1adcc20b78c261d4d052c06057f0a3cb49a15c5d2c00930f710fce2` |
| 9.0 | Hopper: H100 | `hopper-1.9.4` | `linux/amd64` | `sha256:b1f4a9555fd393ff2ec59e89cdcb69476f723c80b773760fae15c99ed211db30` |
| 10.0 | Blackwell: B200 | `100-1.9.4` | `linux/amd64` | `sha256:6aaf0c183e7080dd173b93b9c4002fa569216adb6609d8bc869b55c1787b236c` |
| 12.0 | Blackwell: RTX 50xx | `120-1.9.4` | `linux/amd64` | `sha256:bd8e5b1954146f7fe8590b64b959bc194433c6c38c036592a84d736841ca9400` |
| 12.1 | Blackwell: DGX Spark GB10 | `121-1.9.4` | `linux/arm64` | `sha256:a8f7f1b33b18a8a8d87b9f16268803ce83c9b4dd33fc05424b3b5375bf18c85c` |

Turing и Blackwell images помечены в TEI docs experimental. Для Turing Flash
Attention по умолчанию выключен из-за precision issues; начальная конфигурация
сохраняет этот default. `121-1.9` в общей таблице docs описан как multi, но
проверенный **patch tag `121-1.9.4` содержит только Linux arm64**, помимо
attestation entry `unknown/unknown`. Здесь authoritative evidence — опубликованный
manifest и build matrix выбранной версии. [T2–T3, G1]

### Драйвер и Container Toolkit

Нужны рабочий NVIDIA driver на host и NVIDIA Container Toolkit для Docker.
Установка полного CUDA toolkit в Poetry environment не требуется: runtime CUDA
лежит в TEI image. Официальная настройка toolkit включает
`sudo nvidia-ctk runtime configure --runtime=docker` и перезапуск Docker;
этот перезапуск может затронуть самостоятельно поднятый Qdrant, поэтому это
предварительная операция владельца целевой машины, не скрытый шаг нашего launcher. [N3]

В общей TEI hardware-документации написано «CUDA 12.2 or higher». Однако конкретный
**Dockerfile v1.9.4 использует CUDA 12.9.1 runtime** и пакет `cuda-compat-12-9`.
Entrypoint выбирает compatibility libraries по результату `nvidia-smi`.
Поэтому одной проверки «драйвер показывает CUDA 12.2» недостаточно. [T2, T8]

Для обычного Linux x86_64 рекомендовать поддерживаемый для фактической GPU
драйвер **не старее 575.57.08**, соответствующий CUDA 12.9 Update 1, либо более
новую совместимую ветку. Это консервативное требование выбранного пути, а не
утверждение, что более старые драйверы всегда неработоспособны. NVIDIA допускает
CUDA 12.x minor compatibility с 525.60.13, но с ограничениями features/PTX;
forward compatibility также имеет ограничения оборудования. Проверять старую
ветку драйвера нужно отдельно, не превращать таблицу minimum в обещание запуска.
Для DGX/arm64 действуют поддерживаемые платформой driver packages. [N4–N6]

### Несколько GPU

Candle backend TEI v1.9.4 создаёт **`Device::new_cuda(0)`**. Это одна видимая
GPU, а не tensor parallelism: `--gpus all` не объединяет VRAM нескольких устройств.
Docker/NVIDIA поддерживают выбор device по UUID; launcher передаёт одну GPU через
`--gpus device=GPU-…`. Внутри контейнера она становится device 0. [T7, N7]

Для первой поставки дополнительные GPU свободны. Несколько независимых TEI replicas
и распределение batches можно рассматривать после измерений, но это отдельная
задача concurrency, routing и identity; её не добавлять в стартовую архитектуру.
В проверенном Candle source есть ветка CPU fallback при ошибке определения
совместимости. Поэтому `/health=200` ещё не доказывает GPU inference: smoke должен
проверить startup logs и процесс/занятую память GPU через `nvidia-smi`. [T7]

## HTTP-контракт и точная граница identity

Рекомендуется native TEI `POST /embed`, JSON request с массивом строк:

```json
{
  "inputs": ["query: сварщик без опыта"],
  "normalize": true,
  "truncate": false,
  "truncation_direction": "right"
}
```

Wire enum принимает `right` и `Right`; отсутствие поля тоже выбирает правое
усечение. Клиент передаёт `right` явно из согласованной encoding policy.
Контракт длины — 512 tokens **вместе с prefix и special tokens**; char slicing
не эквивалентен tokenizer truncation. [T5–T6, T9]

Предлагаемая политика: query длиннее лимита отвергается (`truncate=false`),
документ явно обрезается справа (`truncate=true`) после добавления `passage: `.
Это product choice для spec, а не внешнее требование модели. Если spec выберет
иную политику, её нужно согласованно перенести в client request и recipe.
Server запускается с **`--auto-truncate false`**; каждый request передаёт своё
`truncate` явно. В 1.9.4 server default равен **true**, несмотря на старые примеры
и отдельные OpenAPI schema annotations `false`; source использует
`req.truncate.unwrap_or(info.auto_truncate)`. [T4–T6]

Ответ `/embed` — голая JSON-матрица `[[number, …], …]`, в том же порядке, что
inputs. Даже одиночная строка возвращает одну вложенную строку матрицы.
Это не OpenAI envelope `data[].embedding`. Клиент проверяет число vectors,
размерность 768, конечные числа без bool, ненулевую norm и согласованность
normalization с численным допуском; ошибочный batch отвергается целиком. [T5–T6]

`GET /info` возвращает среди прочего:

- `model_id`, `model_sha`, `model_dtype`, `version`, `sha`, `docker_label`;
- `model_type: {"embedding": {"pooling": "mean"}}`;
- `max_input_length`, `max_batch_tokens`, `max_client_batch_size`,
  `max_concurrent_requests`, `auto_truncate`. [T5]

**`model_sha` не является вычисленным checksum загруженных весов.** В TEI 1.9.4
он присваивается непосредственно из аргумента `revision`. При `--revision main`
сервер может вернуть `"main"`, а без аргумента — `null`; загрузка с Hub при этом
использует `main`. При local model directory строка revision тоже не аттестует
содержимое этого каталога. Для выбранного Hub-only пути launcher всегда передаёт
полный проверенный HF commit; клиент требует точного совпадения `model_id` и
`model_sha`, типа embedding, pooling, dtype, input length и runtime version.
Это detection конфигурационного рассогласования внутри доверенного контура,
а не независимая криптографическая аттестация файлов remote endpoint. [T5]

В `/info` **нет embedding dimension, normalization policy, default prompt и
GPU identity**. Размерность и norm проверяются настоящим `/embed` probe.
Query/document prefixes принадлежат приложению и добавляются ровно один раз;
TEI запускается без `--default-prompt`, без `--default-prompt-name`, без
дополнительного `prompt_name` в request. Развёртывание владеет этой проверкой,
поскольку `/info` не позволяет её вывести. Нельзя считать совпадение одной
размерности или `served_model_name` доказательством embedding space. [T4–T6]

HTTP ошибки `/embed` включают 400 для пустого batch, 413 для oversized batch,
422 для tokenization, 424 для backend/model error, 429 для overload. Timeout
не превращается в пустой vector и не запускает другой encoder. Для первой версии
разумен fail-fast без автоматических retries, согласованный с прежними clients
проекта. Повтор после исправления причины — явный новый запуск. [T6]

## Один источник конфигурации — Hydra

Разделить **ownership**, сохранив общие значения через interpolation:

| Группа | Собственные параметры |
| --- | --- |
| Embedding profile | model ID, immutable revision, dimensions, mean pooling, normalization, query/document prefixes, token limit, dtype, truncation policy |
| TEI runtime | image с digest, GPU UUID, container name, loopback publish port, cache directory, max batch tokens/client batch/concurrent inputs, startup deadline, container logging policy |
| TEI client | endpoint, socket timeout, response-size bound, nullable authorization из environment; ожидаемые model/pooling/dtype берутся из embedding profile |
| Corpus indexing | reader/путь, текстовые поля, размер application batch, последовательное исполнение, Qdrant upload batch; embedding client переиспользуется |
| Qdrant connection | URL, timeout, nullable API key через environment, collection identity; серверный lifecycle и storage здесь отсутствуют |

Model ID/revision/pooling/dtype в server argv и client expected identity —
**interpolations того же профиля**, а не независимо повторённые defaults.
Фиксированная длина 512 подтверждается `/info`; у выбранного TEI CLI нет отдельного
`--max-input-length`, который следовало бы выдумывать. Нельзя делать
`max_batch_tokens < 512`: это или изменит effective limit при auto-truncation,
или приведёт к startup error при выключенной auto-truncation. [T4–T5]

Минимальная проектная реализация запуска — `src/integrations/embedding/launch.py`
с Hydra entry point и узкой функцией построения Docker argv. Новый общий root
`configs/vector.yaml` компонует `embedding.model`, `embedding.runtime`, clients
и выбранный indexing/search experiment. Его читают как launcher, так и прежний
`src.run --config-name vector`; default `configs/main.yaml` не меняется.
Launcher читает те же groups, валидирует конфиг, запускает `subprocess`
с **argv list и без shell**, ждёт ограниченное время
readiness и печатает безопасное summary. Он не становится `BaseExperiment`,
не регистрируется transformer и не получает управление Qdrant. Это ещё
**не существующая команда**; предлагаемый вызов после реализации:

```sh
poetry run python -m src.integrations.embedding.launch --config-name vector
```

Запуск контейнера строится из Hydra: Docker `run --detach`, один `--gpus device=…`,
`--publish 127.0.0.1:<host-port>:80`, persistent bind mount на `/data`, restart/log
policy, pinned image и TEI flags из профиля. TEI слушает `0.0.0.0:80` **внутри
контейнера**; `127.0.0.1` внутри контейнера не заменяет loopback binding host.
Cache передаётся только TEI, корпус и Qdrant directory контейнеру не нужны. [D1, T8]

До Docker side effect проверяются обязательные GPU/image/cache settings,
порт и существование container name. Самый простой начальный lifecycle —
ошибка при занятом имени, без неявного remove/recreate чужого контейнера.
Неуспешный startup не объявляется готовым сервисом; имя созданного контейнера
остаётся в диагностике для адресного `docker logs`/`docker stop`.
Не нужен второй Compose YAML, `.env` с model defaults или shell script с
самостоятельными параметрами модели. Docker inspect/logs не публикуются целиком,
если runtime когда-либо получает secrets.

## Batch, timeouts, probes и storage

Начальные числа — **проектные осторожные настройки для smoke**, не измеренный
оптимум и не гарантии для ещё неизвестной VRAM:

- TEI `max_batch_tokens=4096`, `max_client_batch_size=8`,
  `max_concurrent_requests=32`, `auto_truncate=false`.
- Application import batch — 8 documents, **один HTTP batch в полёте**.
  Query — один input. Не запускать полный import параллельно интерактивной сессии
  до измерения latency. TEI считает permits по input внутри client batch, поэтому
  concurrent limit нельзя интерпретировать только как количество HTTP sockets. [T4, T6]
- HTTP socket timeout — 60 s для embed; startup deadline — 600 s, включая первую
  загрузку модели. Эти значения живут в Hydra и изменяются по evidence целевой
  машины. Timeout `urllib` не доказывает, что remote GPU вычисление отменилось;
  server startup и per-request timeout — разные контракты.
- Прогресс readiness: container running → `/health` → `/info` identity → реальный
  query/document `/embed` → проверка GPU → маленький composed Qdrant smoke.
  `/health` сам по себе не подтверждает ни модель, ни качество retrieval. [T5–T7]

`max_batch_tokens` управляет внутренним batch TEI, `max_client_batch_size` —
числом inputs в одном HTTP request, application batch — orchestration импорта,
а размер Qdrant upload batch — отдельная операция хранения. Они не обязаны
совпадать. При OOM сначала уменьшать внутренний token batch/число inputs после
фиксации hardware evidence, сохраняя model revision и encoding policy. [T4]

У E5-base файл `model.safetensors` выбранного revision имеет **1,112,201,288 bytes**;
tokenizer JSON — 17,082,660 bytes. Проверенный Turing 1.9.4 manifest содержит
**3,055,416,019 bytes сжатых layers**; распакованный image занимает больше,
а размер VRAM не равен размеру weight file. Cache монтируется постоянно, чтобы
рестарт не требовал повторной загрузки. На целевой машине заранее оценить также
корпус, Qdrant index, временную generation и артефакты. [M5, G1]

На исследовательском VPS `df -h /` показал 109G total, 93G used, 12G available,
90% utilization. Поэтому **не выполнялись** pull, установка ML dependencies,
скачивание весов, запуск TEI/Qdrant и GPU benchmark. Получены только текстовые
docs, source и небольшие registry/model metadata.

## Networking и auth при внешнем Qdrant

Poetry-приложение на host обращается к опубликованному `127.0.0.1:<tei-port>` и
независимо к `qdrant.url`. Поэтому Docker bridge DNS, `host.docker.internal`,
`network_mode: host` и shared Compose network здесь не нужны. Если Qdrant слушает
на том же host, приложение использует его доступный host port; если на другом —
его сетевой URL. TEI сам к Qdrant не подключается.

Docker publishing без host IP открывает порт наружу. Выбранный launcher задаёт
loopback явно. Документация Docker предупреждает: в версиях **до 28.0.0** localhost
published ports могли быть доступны соседям в L2-сегменте. Для localhost-only
контура рекомендовать Docker Engine 28+ или подтверждённую сетевую изоляцию
целевой машины, не считать старый bind достаточной гарантией. [D1]

Для одного доверенного host nullable TEI auth допустим как явная конфигурация.
Если нужен API key, TEI принимает `API_KEY` через environment и ожидает
`Authorization: Bearer …`; `/info` защищается тем же middleware, но `/health`,
`/metrics` и Swagger относятся к public routes. Ключ не обеспечивает TLS.
Прямой внешний exposure в первую поставку не включать. [T4, T6]

Qdrant credentials предоставляет владелец сервера; импорт требует соответствующих
прав на новую коллекцию, поиску административные права не нужны. Конкретный
Qdrant auth/permission contract фиксируется в основной spec отдельно. Secrets
разрешаются через environment, не CLI overrides и не artifact. Launcher и
интеграции не выводят resolved Hydra config: текущий README уже объясняет,
что `--cfg job --resolve` может раскрыть secrets.

Если Hydra читает ключ из `TEI_API_KEY`, launcher передаёт его в environment
дочернего Docker CLI под именем `API_KEY`, а в argv ставит **`--env API_KEY`**.
Docker документирует наследование значения по имени; `--env API_KEY=value`
здесь не нужен. Так значение не оказывается в argv или сгенерированном command
preview. Это не скрывает ключ от Docker administrator / container inspection;
полный inspect не входит в публикуемый acceptance artifact. [D3]

### Условный прямой Docker-вызов для подтверждённой T4

Ниже **существующий внешний Docker CLI**, а не команда уже написанного проекта.
Это пример materialized output будущего Hydra launcher для Linux x86_64 + T4,
а не второй поддерживаемый набор defaults. Перед использованием UUID заменить
точным из `nvidia-smi -L`, создать выбранный cache directory, проверить драйвер
и согласовать порт. `API_KEY` при использовании auth уже находится в environment.
Если профиль выбрал отсутствие auth, launcher опускает `--env API_KEY`.

```sh
docker run --detach \
  --name recsys-tei \
  --gpus device=GPU-REPLACE-WITH-VERIFIED-UUID \
  --publish 127.0.0.1:8080:80 \
  --mount type=bind,source=/srv/recsys/tei-cache,target=/data \
  --restart unless-stopped \
  --log-driver local \
  --env API_KEY \
  ghcr.io/huggingface/text-embeddings-inference:turing-1.9.4@sha256:f78d17832f00f59f7d52e4b3b8495264257de3c18b773357a00ee341b2af852d \
  --model-id intfloat/multilingual-e5-base \
  --revision d128750597153bb5987e10b1c3493a34e5a4502a \
  --pooling mean \
  --dtype float16 \
  --auto-truncate false \
  --max-batch-tokens 4096 \
  --max-client-batch-size 8 \
  --max-concurrent-requests 32 \
  --hostname 0.0.0.0 \
  --port 80
```

Флаги Docker подтверждены официальным CLI reference; TEI flags — source 1.9.4.
Вызов здесь не исполнялся. [D3, T4]

## Конкретный порядок переноса

1. На целевой машине получить hardware/OS/driver/Docker evidence командами выше.
   Владелец машины обеспечивает Docker + NVIDIA Container Toolkit и существующий
   Qdrant; зафиксировать URL, версию и доступы. На этой стадии выбирается один
   проверенный image/digest и GPU UUID.
2. Перенести **реализованную версию репозитория с точным Git commit**, включая
   `src/`, `configs/`, `poetry.lock` и авторские fixtures. Получить clean checkout
   обычным Git clone/fetch на целевой машине; не копировать `.venv`, outputs,
   credentials и runtime caches. Private корпус доставляется отдельно в
   отдельный путь. Исследовательская заметка не заменяет реализованный checkout.
3. В корне checkout выполнить уже существующую команду `poetry install`.
   Это установка locked application dependencies, без PyTorch/vLLM.
   Заполнить один Hydra site profile: внешнее подключение Qdrant, корпус/поля,
   GPU UUID, image digest, cache/output paths. Server/client model settings
   ссылаются на один embedding profile.
4. Выполнить **будущий Hydra TEI launcher**. Он материализует Docker argv из
   профиля; вручную дублировать model/revision в отдельной deployment-конфигурации
   не требуется. Дождаться перечисленных probes. Для проверки выбранного tag
   до загрузки существует внешний CLI `docker buildx imagetools inspect IMAGE`
   при установленном buildx; он читает registry manifest. [D2]
5. Выполнить **будущий preparation experiment через существующий `src.run`** на
   небольшом корпусе. Проверить import → query embedding → внешний Qdrant →
   исходные vacancy IDs/fields и filters. После этого тем же профилем построить
   полную новую collection generation и запустить будущие vector interactive /
   notebook / batch compositions. Все имена новых Hydra wrappers пока проектные.
6. Проверить TEI restart с постоянным cache и повторный поиск; записать runtime
   identity, GPU, driver, размер корпуса/индекса и timings в acceptance evidence.
   Не перезапускать Qdrant из нашего launcher. Persistence/backup сервера
   подтверждает владелец Qdrant, приложение проверяет доступность готовой generation.

Невозможно сейчас честно заполнить hardware-specific site profile: неизвестны
точная GPU/VRAM, число устройств, архитектура host, драйвер, Docker, Container
Toolkit, свободный диск целевой машины, версия/URL/auth Qdrant, объём и длины
документов. Эти неизвестные не мешают завершить архитектуру и локальные contracts;
они являются входами переносимого target preflight и live acceptance.

## Первичные источники

- **T1:** [TEI v1.9.4 release, 2026-09-15](https://github.com/huggingface/text-embeddings-inference/releases/tag/v1.9.4).
- **T2:** [TEI supported models/hardware, source v1.9.4](https://github.com/huggingface/text-embeddings-inference/blob/v1.9.4/docs/source/en/supported_models.md).
- **T3:** [TEI build matrix](https://github.com/huggingface/text-embeddings-inference/blob/v1.9.4/.github/workflows/matrix.json) и [patch-tag publishing workflow](https://github.com/huggingface/text-embeddings-inference/blob/v1.9.4/.github/workflows/build.yaml).
- **T4:** [TEI CLI flags/defaults, v1.9.4](https://github.com/huggingface/text-embeddings-inference/blob/v1.9.4/router/src/main.rs).
- **T5:** [TEI model loading, Info construction/schema, max length](https://github.com/huggingface/text-embeddings-inference/blob/v1.9.4/router/src/lib.rs).
- **T6:** [TEI HTTP handlers/auth/order/errors](https://github.com/huggingface/text-embeddings-inference/blob/v1.9.4/router/src/http/server.rs) и [wire types](https://github.com/huggingface/text-embeddings-inference/blob/v1.9.4/router/src/http/types.rs).
- **T7:** [TEI Candle device selection и GeLU warning](https://github.com/huggingface/text-embeddings-inference/blob/v1.9.4/backends/candle/src/lib.rs).
- **T8:** [TEI Dockerfile CUDA 12.9.1](https://github.com/huggingface/text-embeddings-inference/blob/v1.9.4/Dockerfile-cuda) и [driver compatibility entrypoint](https://github.com/huggingface/text-embeddings-inference/blob/v1.9.4/cuda-entrypoint.sh).
- **T9:** [TEI tokenization/truncation](https://github.com/huggingface/text-embeddings-inference/blob/v1.9.4/core/src/tokenization.rs).
- **M1:** [E5-base model card на закреплённом revision](https://huggingface.co/intfloat/multilingual-e5-base/blob/d128750597153bb5987e10b1c3493a34e5a4502a/README.md).
- **M2:** [E5-base architecture/dimension config](https://huggingface.co/intfloat/multilingual-e5-base/blob/d128750597153bb5987e10b1c3493a34e5a4502a/config.json).
- **M3:** [E5-base pooling](https://huggingface.co/intfloat/multilingual-e5-base/blob/d128750597153bb5987e10b1c3493a34e5a4502a/1_Pooling/config.json) и [max_seq_length](https://huggingface.co/intfloat/multilingual-e5-base/blob/d128750597153bb5987e10b1c3493a34e5a4502a/sentence_bert_config.json).
- **M4:** [HF model API, observed sha](https://huggingface.co/api/models/intfloat/multilingual-e5-base).
- **M5:** [HF file-size metadata на закреплённом revision](https://huggingface.co/api/models/intfloat/multilingual-e5-base/tree/d128750597153bb5987e10b1c3493a34e5a4502a?recursive=false&expand=false).
- **S1:** [Sentence Transformers installation, official source](https://github.com/huggingface/sentence-transformers/blob/ff69486771998c07008ebd3c7e05e827f03df46f/docs/installation.md).
- **S2:** [SentenceTransformer API source, fetched commit](https://github.com/huggingface/sentence-transformers/blob/ff69486771998c07008ebd3c7e05e827f03df46f/sentence_transformers/sentence_transformer/model.py).
- **V1:** [vLLM embedding models/API, fetched commit](https://github.com/vllm-project/vllm/blob/2bea22002d66a6c89d208fa682b40a89538f023c/docs/models/pooling_models/embed.md).
- **V2:** [vLLM pooling configuration, fetched commit](https://github.com/vllm-project/vllm/blob/2bea22002d66a6c89d208fa682b40a89538f023c/docs/models/pooling_models/README.md).
- **V3:** [vLLM CUDA/PyTorch compatibility, fetched commit](https://github.com/vllm-project/vllm/blob/2bea22002d66a6c89d208fa682b40a89538f023c/docs/getting_started/installation/gpu.cuda.inc.md).
- **N1:** [NVIDIA nvidia-smi documentation](https://docs.nvidia.com/deploy/nvidia-smi/index.html).
- **N2:** [NVIDIA GPU compute-capability table](https://developer.nvidia.com/cuda-gpus).
- **N3:** [NVIDIA Container Toolkit installation/configuration](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).
- **N4:** [CUDA 12.9 Update 1 release notes: driver tables](https://docs.nvidia.com/cuda/archive/12.9.1/cuda-toolkit-release-notes/index.html).
- **N5:** [NVIDIA CUDA minor-version compatibility и ограничения PTX](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html).
- **N6:** [NVIDIA forward compatibility и supported configurations](https://docs.nvidia.com/deploy/cuda-compatibility/forward-compatibility.html).
- **N7:** [NVIDIA GPU enumeration для Docker](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/docker-specialized.html).
- **D1:** [Docker port publishing/loopback semantics](https://docs.docker.com/engine/network/port-publishing/).
- **D2:** [Docker buildx imagetools inspect](https://docs.docker.com/reference/cli/docker/buildx/imagetools/inspect/).
- **D3:** [Docker run: environment, mounts, GPU, restart policy](https://docs.docker.com/reference/cli/docker/container/run/).
- **G1:** [Официальный GHCR package](https://github.com/huggingface/text-embeddings-inference/pkgs/container/text-embeddings-inference); 2026-10-10 registry `GET /v2/huggingface/text-embeddings-inference/manifests/<tag>` вернул digest/platforms таблицы. Получение metadata использовало anonymous public-registry access; image layers не запрашивались. Digest конкретной Linux amd64 manifest Turing: `sha256:120811b92c9416570b1e0813c80cc1d36c9a3ba3db11eb8b8b3ea734c3e0f836`.
