# Кодирование запросов и вакансий для vector-only поиска

Дата: **2026-10-10**. Исследование для [проекта spec 008](../specs/008-vector-search.md).
Это рекомендация архитектуры и первого профиля, а не реализованный контракт или
доказательство качества поиска. Прочитаны первичные model cards, конфигурации моделей
и исходники TEI. Веса не скачивались, inference, GPU и Qdrant не запускались.

## Рекомендация первого профиля

Начать с **`intfloat/multilingual-e5-base`**, полного вектора **768**, masked mean
pooling, L2 normalization, cosine retrieval, `query: ` для запросов и `passage: `
для документов. Максимум — **512 токенов вместе с prefix и special tokens**.
Для коротких запросов превышение отклонять; длинные документы обрезать справа
после детерминированного формирования текста. Первый профиль использует обычный
FP16 inference без квантования весов и без сокращения размерности. [M1, C1, T1]

Выбор основан на небольшом размере модели, простом документированном retrieval
контракте, поддерживаемой в TEI архитектуре XLM-RoBERTa и опубликованной проверке
на русском разделе Mr. TyDi. Это аргументы для первого воспроизводимого эксперимента,
а не утверждение, что E5-base лучше BGE-M3 или Qwen3 на вакансиях. У модели около
278 млн параметров; только FP16 параметры занимают примерно 0.52 GiB. Это нижняя
оценка арифметики хранения, **не требование VRAM**: отдельно нужны activations,
runtime buffers и память batch. Тип GPU, VRAM и число доступных устройств пока
неизвестны; «NVIDIA T…» не подтверждает T4 или совместимость конкретного образа. [H1]

Следующий кандидат при заметной потере информации за 512 токенами — BGE-M3 dense;
Qwen3-Embedding-0.6B — независимый instruction-aware кандидат. E5-large проверяет
эффект большей модели при том же коротком окне. Qwen3-Embedding-4B имеет смысл
после проверки GPU и corpus eval; наличие GPU само по себе не обосновывает его
выбор. Первый runtime не должен автоматически менять модель при нехватке памяти.

## Проверенные модели и encoding contract

| Модель | Полная размерность | Документированный предел | Pooling и normalization | Входы retrieval |
| --- | ---: | ---: | --- | --- |
| multilingual-e5-base | 768 | 512 | Masked mean; L2 | Query `query: …`; document `passage: …`, включая русский язык |
| multilingual-e5-large | 1024 | 512 | Masked mean; L2 | Те же два английских prefix |
| BAAI/bge-m3, dense only | 1024 | 8192 | CLS; L2 | Текст запроса и документа без retrieval instruction |
| Qwen3-Embedding-0.6B | 1024 | 32K | Last non-padding token; L2 | Query `Instruct: <task>\nQuery:<text>`; document без instruction |
| Qwen3-Embedding-4B | 2560 | 32K | Last non-padding token; L2 | Тот же instruction contract |

Основания таблицы: model cards [M1–M5], pooling/model/tokenizer configs [C1–C5]
и реализация normalization TEI [T5]. У BGE-M3 в этой архитектуре используется
только один dense vector: наличие у исходной модели sparse/ColBERT возможностей
не означает включение этих режимов. Card BGE-M3 прямо говорит, что query instruction
больше не нужен. E5 требует prefix даже для неанглийских текстов. [M1–M3]

Qwen поддерживает Matryoshka dimensions: 32–1024 у 0.6B, 32–2560 у 4B.
Сокращение размерности меняет профиль; оно не предлагается в первом эксперименте.
TEI умеет обрезать вектор до `dimensions`, затем нормализовать. Наличие параметра
у runtime не доказывает пригодность произвольного сокращения для E5/BGE. [M4, M5, T5]

Для Qwen авторы рекомендуют английскую инструкцию и называют улучшения на своих
задачах; перенос этих чисел на вакансии не подтверждён. Возможный task для отдельного
профиля: `Given a job search query, retrieve relevant job vacancies matching the
requested role, skills, and working conditions`. Это наша проверяемая гипотеза,
а не инструкция из обучения и не средство гарантировать строгие ограничения.

Есть существенное расхождение источников Qwen: обе cards называют 32K, tokenizer
configs содержат `model_max_length=131072`, а model configs — 32768 для 0.6B и
40960 для 4B. Пример Transformers в cards использует 8192. Нельзя выводить
поддерживаемое окно из одного tokenizer числа: для будущего профиля нужен явный
operational cap не выше документированных 32K и подтверждение фактического TEI
`max_input_length`. TEI может ограничить его ещё и `max_batch_tokens`. [C4, C5, T2]

### Проверенные immutable revisions

SHA получены из first-party Hugging Face model API; cards/configs затем читались
по этим SHA, а не по движущемуся `main`. [H1–H5]

| Repository | Revision |
| --- | --- |
| `intfloat/multilingual-e5-base` | `d128750597153bb5987e10b1c3493a34e5a4502a` |
| `intfloat/multilingual-e5-large` | `3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3` |
| `BAAI/bge-m3` | `5617a9f61b028005a4858fdac845db406aefb181` |
| `Qwen/Qwen3-Embedding-0.6B` | `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` |
| `Qwen/Qwen3-Embedding-4B` | `5cf2132abc99cad020ac570b19d031efec650f2b` |

TEI проверен на **v1.9.4**, commit
`e80ef225ed0e6cb1717ce632a6a84b6cf211bb67`. Его README и backend code поддерживают
XLM-RoBERTa и Qwen3; это source-level evidence поддержки архитектур, не выполненный
smoke каждого model/GPU/image сочетания. Образ целевого GPU следует pin-ить digest
после выбора совместимого варианта. Не копировать устаревшие Docker snippets из
cards: в проверенной card Qwen 0.6B CPU/GPU теги 1.7.2 переставлены относительно
сопровождающего текста. [T0, T1, M4]

## Запрос: от исходной строки до semantic embedding

Предлагаемая последовательность, соответствующая текущему immutable Request:

1. Взять snapshot `Request(data.filters)`; потребовать ровно один непустой
   `QueryFilter`. Проверить все явные filters и разрешить `UserPropertyFilter`
   до inference. Исходный текст и filters сохраняются.
2. Для model input выполнить только **NFC + схлопывание whitespace + trim**.
   Это явная версия новой semantic text policy. Сохранить регистр, `ё`, числа,
   знаки, аббревиатуры и отрицания. Не добавлять stemming/lemmatization,
   transliteration, перевод или подмену похожих латинских/кириллических букв.
3. Сформировать ровно один E5 input: `query: ` + подготовленный текст.
   Проверить UTF-8 byte budget. Token budget проверяет тот же tokenizer, который
   использует inference; `truncate=false` не позволяет потерять хвост запроса.
4. Проверить совместимость runtime/profile/collection, выполнить `/embed` и
   получить типизированный `Embedding`. Полностью проверить ответ до Qdrant.
5. Выполнить filtered vector retrieval и только после полной конвертации hits
   заменить `data.items` обычной SERP. Ошибка не становится пустой выдачей.

NFC и whitespace policy — проектная рекомендация, а не требование E5. Tokenizer
дополнительно выполняет собственную модельную нормализацию; приложение не должно
заменять её самодельным аналогом. E5 `sentence_bert_config.json` явно содержит
`do_lower_case=false`. Существующий
[NormalizeText](../../src/components/query/normalization.py) выполняет NFKC и
casefold: это пригодно для lexical matching/словарей, но его повторное использование
меняет semantic input. Лучше маленькая функция владельца encoding policy, чем
изменение поведения существующих lexical экспериментов. [C1]

| Исходный запрос | Model input для E5-base |
| --- | --- |
| `  Комплектовщик   ТСД  2/2 ` | `query: Комплектовщик ТСД 2/2` |
| `Водитель категории C без опыта` | `query: Водитель категории C без опыта` |
| `оператор ЧПУ не наладчик` | `query: оператор ЧПУ не наладчик` |
| `электромонтёр 4 разряда` | `query: электромонтёр 4 разряда` |
| `комплетовщик Москва` | `query: комплетовщик Москва` |
| `C++ .NET 1С` | `query: C++ .NET 1С` |

`C`, `С`, `C++`, `C#`, `.NET`, `1С`, `ЧПУ`, `2/2`, `5/2` и разряды не разбиваются
и не переписываются приложением. В baseline не нужен механизм protected spans:
нет операции, изменяющей их содержимое. При последующем opt-in spelling/layout
эксперименте защита и provenance исправления понадобятся вновь.

Отрицание `не наладчик` и фраза `без опыта` остаются текстом. Embedding similarity
не обеспечивает Boolean NOT или обязательное соблюдение квалификации. Строгие
условия пользователь выражает существующими явными filters. Если продукт потребует
превращать отрицание в обязательное исключение, это отдельный accepted contract.

Не переносить [lexical parser/rewriter](../../src/components/query/jobboard/rewrite.py)
механически. Там aliases — альтернативы DSL, coverage — условие допуска, а rewrite
сохраняет исходную lexical ветку через `dis_max`. Конкатенация aliases внутри одного
embedding меняет семантический текст и относительный вклад понятий; склейка original
и rewritten также не эквивалентна двум поисковым веткам. Первый vector-only путь
кодирует исходный смысловой текст без expansion и исправлений. Опечатки, разговорные
названия и layout mistakes становятся отдельными срезами eval; устойчивость модели
к ним нельзя предположить по multilingual маркировке.

## Вакансия: configurable fields и token truncation

`VacancyText` — чистое преобразование `Vacancy.fields` в текст по Hydra recipe;
reader корпуса и embedding модели не должны знать физические имена полей.
Первый preset задаёт `title`, затем `description`, разделённые `\n\n`, без повторения
title, искусственного boost или добавления несуществующих навыков. Любые другие
строковые поля выбираются явным упорядоченным списком. Это порядок при сохранении
входа и обрезке, **не гарантия большего веса title в attention или итоговом score**.

Для первой реализации достаточно literal top-level field names; вложенные paths,
массивы и сборка из объектов требуют конкретного reader/renderer и явного контракта.
Не сериализовать произвольный JSON целиком как текст. Missing/null/blank optional
поле пропускается; present non-string и отсутствие любого непустого текстового
содержимого — ошибка до записи коллекции. Required/optional определяется recipe;
пустой title не обязан исключать документ с содержательным description, если title
не объявлен required. Исходные полные fields сохраняются отдельно без mutation.

После NFC и whitespace policy для каждого поля: `passage: ` + части по порядку.
Для title/description preset сохраняется начало title, затем доступная часть
description. Текст, целиком состоящий из слишком длинного title, также обрезается
справа; baseline не обещает полное сохранение произвольно длинного title. Отдельный
per-field token allocation или title reservation имеет смысл только как принятый
экспериментальный рецепт. Хвост descriptions, дополнительные поля, зарплата и
условия в конце могут не попасть в вектор: исходные payload fields это не исправляют.

Политика первого профиля — right token truncation до 512 **после** добавления prefix
и special tokens. Нельзя считать «512 символов» или «512 слов» эквивалентом 512
токенов. Tokenizer E5 использует предел 512; `max_position_embeddings=514` не даёт
два дополнительных пользовательских токена. TEI учитывает RoBERTa position offset
и Sentence Transformers max sequence length. [C1, T2, T4]

HTML processing не включать неявно. Если source fields содержат HTML, выбор между
готовым plain-text полем и конкретной HTML-to-text политикой должен быть принят
до сборки. Strip-tags regex, HTML unescape и обработка block/list separators меняют
представление документа. Такой renderer должен иметь версию и tests; embeddings
пересобираются при его смене. Содержание HTML не исполняется.

Одна вакансия даёт один вектор. Chunking, pooling по чанкам, title-only + description
multivectors и LLM summary находятся за пределами первого профиля. Доля документов,
затронутых truncation, и расположение важной информации — обязательные вопросы
первого анализа корпуса перед оценкой модели.

## TEI HTTP: явные prompts, limits и batch policy

`TextEmbedder` сам формирует model inputs. TEI запускается **без**
`--default-prompt`/`--default-prompt-name`; клиент не посылает `prompt_name`.
Это исключает двойной prefix и применение query prefix к документам. В TEI prefix
добавляется до tokenization, а `prompt_name` выбирает named prompt только если
такой key есть в Sentence Transformers config. У pinned E5-base файла с named
prompts нет; `prompt_name="query"` нельзя считать поддержанным. [T2–T4, C1]

Иллюстрации фактического body для native `POST /embed`:

```json
{
  "inputs": ["query: Водитель категории C без опыта"],
  "normalize": true,
  "truncate": false,
  "truncation_direction": "right"
}
```

```json
{
  "inputs": [
    "passage: Комплектовщик\n\nСборка заказов с ТСД. График 2/2. Обучение на месте.",
    "passage: Водитель-экспедитор категории C\n\nДоставка товаров по Москве."
  ],
  "normalize": true,
  "truncate": true,
  "truncation_direction": "right"
}
```

Native ответ — `list[list[float]]`, даже для одного текста; его порядок соответствует
порядку inputs. `dimensions` в первом профиле не передаётся. TEI default normalize
равен true, но клиент всё равно указывает его явно. Отсутствующий `truncate` берётся
из `info.auto_truncate`; в v1.9.4 CLI default равен true, несмотря на false в некоторых
schema annotations. Поэтому implicit default непригоден как encoding contract. [T3, T5, T6]

Wire значение `"right"` подтверждено `serde(alias = "right", alias = "Right")`
у варианта `Right`; enum также принимает `"Right"`. Примеры используют lowercase,
как schema annotation. Это проверка десериализации, а не вывод по имени Rust variant. [T3]

Стартовые **проектные**, а не измеренные бюджеты:

| Уровень | Предложение |
| --- | --- |
| Запрос | ≤4096 UTF-8 bytes до HTTP; ≤512 model tokens; overflow — ошибка |
| Один prepared document input | ≤65536 UTF-8 bytes; больший input — ошибка, без скрытого byte slicing; model truncation справа до 512 |
| HTTP inference batch | ≤8 документов и ≤262144 bytes фактически сериализованного JSON body |
| Token budget batch | ≤4096 model tokens; для E5-base гарантируется консервативной оценкой `8 × 512` |
| Начальное выполнение | Один batch in flight; без автоматического увеличения concurrency, retries и смены модели |
| Remote TEI | Явные `max_client_batch_size=8`, `max_batch_tokens=4096`, `auto_truncate=false`; запросы всё равно задают truncate явно |

UTF-8 bytes считать после prefix/представления, а общий body — после JSON escaping;
бюджет тела нельзя получить суммированием одних длин текстов. Слишком большой batch
делится детерминированно, слишком большой одиночный документ отклоняется.
TEI имеет отдельные `payload_limit`, `max_client_batch_size` и `max_batch_tokens`;
последний управляет GPU batching, а не общим размером JSON. В проверенном CLI defaults
— 2000000 bytes, 32 inputs и 16384 tokens; использовать их как наши выбранные defaults
без измерений нельзя. [T6]

Для точного анализа lengths есть `POST /tokenize` с теми же prefixed inputs и
`add_special_tokens=true`. Он возвращает tokens без model inference, но выполняет
HTTP и не является offline preview. Подходит для corpus audit и подсчёта доли
truncation без локальной ML зависимости. `/embed` уже валидирует query token limit;
добавлять `/tokenize` в каждый короткий query только ради дублирования этой проверки
не требуется. Bytes budget и tokens budget независимы; модельный tokenizer нельзя
заменить оценкой «примерно четыре символа на token». [T3, T4]

Лимиты и batch size подлежат замеру на целевом GPU. Несколько GPU автоматически
не означают tensor parallelism или объединённую VRAM. Повышение batch/concurrency
не меняет смысл текста, но может менять численные округления; новая dtype,
quantization, модель или revision требуют отдельного profile и новой коллекции.

## Типы, identity и место интерфейса

Рекомендуемая типизированная модель, пока не реализация:

```text
EmbeddingProfile (immutable)
  schema_version
  model_id + immutable revision
  tokenizer identity (тот же pinned repo/revision в первом профиле)
  output_dimensions + pooling + L2 normalization
  query prefix/instruction + document prefix
  query/document text policy versions
  max_tokens + query overflow policy + document truncation direction
  inference dtype + model quantization (= none для первого профиля)

Embedding (immutable)
  profile_id                 # digest канонического полного profile
  values: tuple[float, ...]  # длина = output_dimensions; finite; norm > 0

Collection recipe
  profile + profile_id
  corpus identity + document field mapping/order/renderer version
  runtime version/image digest + checked deployment identity
  collection identity + vector distance
```

Canonical serialization profile должна фиксировать Unicode/number formatting и
порядок ключей; user-facing name профиля не заменяет digest. Document recipe
отделена от embedding space: изменение fields/order меняет generation, даже если
модель прежняя. Compute dtype/квантование меняют численные vectors; wire JSON floats
не сообщают исходную dtype. Storage quantization Qdrant относится к collection
recipe и retrieval approximation, а не к quantization embedding модели. Старт —
без обоих видов quantization. Runtime version сохраняется для provenance; её смена
требует реальной проверки эквивалентности, а не молчаливого принятия cached vectors.

Узкий интерфейс `TextEmbedder.encode_query(text) -> Embedding` и
`encode_documents(texts) -> tuple[Embedding, ...]` объединяет prefix, preprocessing,
limits, identity и response validation. Это один deep module, реально используемый
поиском, подготовкой и notebook. `TEIIntegration` — adapter на HTTP seam; lifecycle
GPU модели принадлежит TEI. Новый base class/strategy hierarchy здесь не требуется.
Hydra выбирает конкретную implementation и передаёт зависимости.

`VectorSearchTransformer.transform(data, *, registry) -> PipelineData` владеет
последовательностью Request → validation → encode → Qdrant → SERP. Embedding живёт
локально в вызове. Существующие `context`, `items`, immutable append-only filters
и `branches` сохраняют смысл. Не добавлять `context["embedding"]`, `Any workspace`,
новый embedding filter или документ-заглушку. Не требуется самостоятельный
`EncodeQueryTransformer`: он потребовал бы нового контракта intermediate payload.
Такое решение принимается лишь при реальной потребности отдельной композиции
операций над query vectors. Основание —
[PipelineData](../../src/core/transformers/data.py) и [архитектура](../architecture.md).

Чистый preview может показывать `prepare_query/plan`: prepared model input,
filters, profile и candidate depth. Он не способен показать **полный** Qdrant
body с настоящим query vector без inference. Если нужен preview embedding,
он явно выполняет HTTP. Не обещать старую семантику offline `build_query()` для
body, вычисление которого требует сети; не делать скрытый двойной encode при
preview, а затем search. Global cache или состояние предыдущего запроса не нужны.

### Подтверждение фактически работающего профиля

TEI `/info` сообщает `model_id`, `model_sha`, `model_dtype`, pooling, input/batch limits
и runtime version. Однако source присваивает `model_sha: revision`: при `--revision main`
это строка `main`, а не resolved weight commit. Нужен запуск с полным проверенным SHA
и сопоставление identity с recipe. Local model directory или незапиненный endpoint
не получают подтверждённую identity только из совпадения dimension. [T2]

`/info` не аттестует отсутствие default prompt и не содержит dimension/полный hash
всех загруженных файлов. Нельзя заявлять криптографическое доказательство весов
через этот endpoint. В управляемом контуре сочетать immutable download revision,
deployment recipe/image digest, проверку `/info` и reference encoding probe.
Эндпоинт не должен балансироваться между разными профилями; pin runtime на время
сборки и поиска. Перед стартом сверять loaded model/dtype/pooling/max length,
а response shape и vectors проверять в каждом вызове.

## Failure semantics и необходимые доказательства

До Qdrant search/upload отвергать неверное число vectors, порядок которого нельзя
сопоставить исходным inputs, неверную dimension, bool/string/null вместо чисел,
NaN/Infinity и нулевой norm. Для L2 профиля проверять norm около 1 с явно выбранным
допуском; первый кандидат допуска — `1e-4`, его пригодность подтверждается реальным
runtime. Не «чинить» NaN или dimension, не достраивать отсутствующие vectors нулями.
Model/profile mismatch — ошибка, даже если dimension одинакова. Не публиковать
частичный batch и не менять `data.items`, если хотя бы один hit/score повреждён.

Локальные fixtures докажут форму HTTP bodies, prefix ровно один раз, правильные
truncate/normalize параметры, byte batching, model identity checks, strict response
validation, AND-filters, сохранение original fields/QueryFilter и отсутствие Qdrant
вызова/partial mutation при отказе. Они не доказывают реальные tokens, embedding
качество, GPU memory fit или ANN recall.

На целевой машине требуются отдельные проверки:

- Pinned TEI с actual GPU: `/info`, exact model SHA, dtype, mean pooling и 512 tokens;
  отсутствие server-side prompt подтвердить deployment и `/tokenize` probe.
- Реальные query и document vectors: 768 finite values, нормализация, один текст
  по отдельности и в batch, одинаковый порядок, допустимое численное расхождение.
- Reference implementation из E5 card на малом наборе с теми же weights/tokenizer,
  pooling и prefixes; сравнение с TEI с допуском, учитывающим FP16. Это проверка
  encoding parity, а не тест на точное побитовое равенство floats.
- Запросы из таблицы выше; точная token boundary 512/513 после prefix и special
  tokens; query overflow должен завершиться до Qdrant, document overflow — дать
  ожидаемую right truncation.
- Корпус: распределение token lengths и truncation, затем judged queries с
  соседними профессиями, квалификациями, negations, опечатками, `ё/е`, mixed scripts,
  short generic и длинными queries. Разделить exact dense retrieval качество
  модели и ANN recall Qdrant относительно exact retrieval по тем же vectors.
- NDCG/Recall по реальным judgments, latency p50/p95, throughput и peak VRAM для
  выбранных input lengths/batches. Синтетический blue-collar набор полезен для
  контрастных проверок, но не доказывает качество на заменяемом реальном корпусе.

Главные unknowns: фактическая GPU/VRAM/driver; структура, HTML и длины источника;
доля полезных данных за 512 tokens; judged качество E5-base против альтернатив;
предельный batch и latency. Они не требуют выдумывать дополнительный framework
или заранее включать lexical fallback.

## Первичные источники

Ссылки на config directories ниже содержат прочитанные `config.json`,
`tokenizer_config.json`, `1_Pooling/config.json`, а для E5/BGE также
`sentence_bert_config.json`; у Qwen — `config_sentence_transformers.json`.

[M1]: https://huggingface.co/intfloat/multilingual-e5-base/blob/d128750597153bb5987e10b1c3493a34e5a4502a/README.md
[M2]: https://huggingface.co/intfloat/multilingual-e5-large/blob/3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3/README.md
[M3]: https://huggingface.co/BAAI/bge-m3/blob/5617a9f61b028005a4858fdac845db406aefb181/README.md
[M4]: https://huggingface.co/Qwen/Qwen3-Embedding-0.6B/blob/97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3/README.md
[M5]: https://huggingface.co/Qwen/Qwen3-Embedding-4B/blob/5cf2132abc99cad020ac570b19d031efec650f2b/README.md
[C1]: https://huggingface.co/intfloat/multilingual-e5-base/tree/d128750597153bb5987e10b1c3493a34e5a4502a
[C2]: https://huggingface.co/intfloat/multilingual-e5-large/tree/3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3
[C3]: https://huggingface.co/BAAI/bge-m3/tree/5617a9f61b028005a4858fdac845db406aefb181
[C4]: https://huggingface.co/Qwen/Qwen3-Embedding-0.6B/tree/97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3
[C5]: https://huggingface.co/Qwen/Qwen3-Embedding-4B/tree/5cf2132abc99cad020ac570b19d031efec650f2b
[H1]: https://huggingface.co/api/models/intfloat/multilingual-e5-base
[H2]: https://huggingface.co/api/models/intfloat/multilingual-e5-large
[H3]: https://huggingface.co/api/models/BAAI/bge-m3
[H4]: https://huggingface.co/api/models/Qwen/Qwen3-Embedding-0.6B
[H5]: https://huggingface.co/api/models/Qwen/Qwen3-Embedding-4B
[T0]: https://github.com/huggingface/text-embeddings-inference/releases/tag/v1.9.4
[T1]: https://github.com/huggingface/text-embeddings-inference/blob/e80ef225ed0e6cb1717ce632a6a84b6cf211bb67/README.md
[T2]: https://github.com/huggingface/text-embeddings-inference/blob/e80ef225ed0e6cb1717ce632a6a84b6cf211bb67/router/src/lib.rs
[T3]: https://github.com/huggingface/text-embeddings-inference/blob/e80ef225ed0e6cb1717ce632a6a84b6cf211bb67/router/src/http/types.rs
[T4]: https://github.com/huggingface/text-embeddings-inference/blob/e80ef225ed0e6cb1717ce632a6a84b6cf211bb67/core/src/tokenization.rs
[T5]: https://github.com/huggingface/text-embeddings-inference/blob/e80ef225ed0e6cb1717ce632a6a84b6cf211bb67/core/src/infer.rs
[T6]: https://github.com/huggingface/text-embeddings-inference/blob/e80ef225ed0e6cb1717ce632a6a84b6cf211bb67/router/src/main.rs
[T7]: https://github.com/huggingface/text-embeddings-inference/blob/e80ef225ed0e6cb1717ce632a6a84b6cf211bb67/router/src/http/server.rs

- [M1] — E5-base: prefixes, mean pooling, normalization, multilingual/RU evidence.
- [M2] — E5-large: тот же encoding contract, 1024 dimensions, 512-token limit.
- [M3] — BGE-M3: dense/8192/1024, отсутствие instruction; FP16 может менять качество.
- [M4], [M5] — Qwen3: instructions, last-token pooling, dimensions и ограничения.
- [C1], [C2], [C3], [C4], [C5] — pinned executable configuration моделей.
- [H1], [H2], [H3], [H4], [H5] — first-party SHA и доступные parameter counts.
- [T0], [T1] — версия TEI и объявленные поддерживаемые архитектуры.
- [T2] — загрузка revision, pooling, model length и `/info`.
- [T3], [T7] — native request/response schemas и выполнение `/embed`/`/tokenize`.
- [T4] — prompt composition, tokenization, truncation и input guards.
- [T5] — dimension truncation, L2 normalization и numerical representation.
- [T6] — CLI defaults, server-side prompts и capacity limits.
