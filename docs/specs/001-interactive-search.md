# 001. Рабочий каркас и интерактивный поиск

Статус: **реализована с локальной проверкой**.
Live smoke выполняется отдельно от локальной приёмки. Результаты проверок
и ограничения описаны в development.md. Основы находятся в
[architecture.md](../architecture.md); фактический статус кода — в
[development.md](../development.md).

## Результат и границы

Репозиторий содержит лаконичную реализацию существующего каркаса и один рабочий
эксперимент. После запуска через Hydra пользователь последовательно вводит текст
и получает SERP вакансий со всеми полями и score. Нормальное завершение сохраняет
результаты сессии через артефакт. Ошибка логируется и завершает процесс.

Обработка выражена transformers и pipeline через именованный registry.
Сохраняются существующие семейства абстракций документов, коллекций, запросов,
фильтров, registry, integrations, experiments, artifacts и exporters. Их черновые
сигнатуры уточняются. Совместимость со старыми ветками и их конфигами не требуется.

В границах этой первой спеки метрики, NDCG, статистика, сравнения, backend
integration и подбор параметров сохраняются как отложенный каркас. Каталоги extractors/aggregation не заполняются
искусственными реализациями. Общая модель поддерживает ветвления, но отдельный
исполнитель ветвлений и параллельное выполнение не входят в первый эксперимент.
Метрики, сравнение, extractors, aggregation и ветвления затем реализованы в
[spec 002](002-evaluation-and-composition.md); переход на Poetry — в
[spec 003](003-cli-completion.md).

## Принятые решения

| Вопрос | Решение этой спеки |
| --- | --- |
| Registry в transformer | Явный keyword-аргумент `registry` у `transform`, без global state или bind |
| Items в pipeline | `BaseDocumentCollections`; результат поиска — `SERP` |
| Score | Поле документа, представляющего конкретное попадание в выдачу |
| Copy | Shallow по умолчанию; explicit deep изолирует документы и контекст |
| QueryFilter | Ровно один непустой текстовый фильтр на поиск |
| Терминал | Полный JSON результата с отступами, без TUI и пагинации |
| Завершение | EOF сохраняет; Ctrl-C прерывает без сохранения |
| Ошибка | Выход с кодом 1, без публикации частичного артефакта |
| Артефакт | Один JSON-файл результатов успешной сессии |
| Setup | Python 3.12+, Poetry 2.2+, Hydra; pytest, Ruff, ty для разработки |

Транспорт использует стандартный формат OpenSearch,
URL и авторизация параметризуются. HTTP-клиент реализован средствами
standard library, без новой runtime-зависимости сверх Hydra.

## 1. Документы и коллекции

Ниже `JSONValue` — рекурсивный JSON-тип, `JSONScalar` —
`str | int | float | bool | None`. Это aliases для аннотаций, не framework
валидации. Сигнатуры с `...` описывают интерфейсы, не готовые реализации.
Методы в абстрактных контрактах должны быть настоящими `abstractmethod`, а не
пустыми методами, которые молча возвращают `None`.

```python
class BaseDocument:
    id: str
    fields: dict[str, JSONValue]
    score: float | None


class Vacancy(BaseDocument): ...


class BaseExternalDocument(ABC):
    def to_document(self) -> BaseDocument: ...


class BaseDocumentCollections:
    items: list[BaseDocument]


class SERP(BaseDocumentCollections): ...


class BaseExternalDocumentCollections(ABC):
    def to_document_collections(self) -> BaseDocumentCollections: ...
```

Для простых данных использовать dataclasses и отдельные mutable containers на
каждый экземпляр. `SERP.items` сохраняет порядок hits без сортировки и дедупликации.
`fields` сохраняет весь `_source`, включая неизвестные поля и вложенные значения.
`id` берётся из `_id`, `score` — из `_score`; `null` сохраняется как `None`.
Поле `score` внутри `_source` остаётся в `fields` и не конфликтует с оценкой поиска.

Объект вакансии принадлежит конкретному результату: одинаковый id в двух поисках
может иметь разные score и независимые данные. Глобальный кэш документов не вводится.
Это позволяет обойтись без отдельной обёртки для каждого search hit.

## 2. Фильтры, Request и PipelineData

```python
class BaseFilter(ABC): ...


@dataclass(frozen=True)
class QueryFilter(BaseFilter):
    text: str


@dataclass(frozen=True)
class TermFilter(BaseFilter):
    field: str
    value: JSONScalar


@dataclass(frozen=True)
class UserPropertyFilter(BaseFilter):
    field: str
    property: str


class BaseRequest(ABC): ...


@dataclass(frozen=True)
class Request(BaseRequest):
    filters: tuple[BaseFilter, ...]


class PipelineData:
    def __init__(
        self,
        *,
        context: Mapping[str, JSONValue] | None = None,
        items: BaseDocumentCollections | None = None,
        filters: Sequence[BaseFilter] = (),
    ) -> None: ...

    context: dict[str, JSONValue]
    items: BaseDocumentCollections

    @property
    def filters(self) -> tuple[BaseFilter, ...]: ...

    def add_filter(self, value: BaseFilter) -> None: ...
    def copy(self, *, deep: bool = False) -> PipelineData: ...
```

Фильтры неизменяемы, включая вложенные значения. У поддержанных типов параметры
scalar, поэтому универсальный recursive-freeze механизм не нужен. Тот же
инвариант обязателен для будущих наследников.

Семантика `UserPropertyFilter`: связать поле индекса с именем
свойства пользователя. Значение извлекается из context при построении DSL;
отсутствующее или не-scalar значение — ошибка до сетевого вызова.

`Request` — неизменяемый снимок условий одного поиска, без DSL, клиента и
результатов. Transformer создаёт его из `data.filters`. Текст живёт только
в `QueryFilter`, а не дублируется в пользовательском контексте.

`PipelineData.filters` доступен только для чтения; добавление выполняется через
`add_filter`, расширяющий внутренний tuple. Setter, delete, replace и clear
отсутствуют. Изменение `data.filters` присваиванием и изменение полей фильтра
должны отклоняться. Защита от намеренного обхода private-полей Python не требуется.

Конструктор копирует внешний dict контекста и контейнер filters. Переданный
items используется как принадлежащий payload результат; без него создаётся
новая пустая коллекция. Контекст и items можно изменять и заменять.

Shallow copy создаёт новый payload, dict, коллекцию того же типа и список items;
сами документы и вложенные значения context остаются общими. Deep copy через
стандартный `deepcopy` изолирует эти данные, включая содержимое `Vacancy.fields`.
Фильтры в обоих случаях остаются общими immutable values, а добавление новых
фильтров независимо для копий. Копирование не меняет исходное состояние.

Payload содержит данные, а не клиентов и ресурсы. Наследники документов должны
поддерживать `deepcopy`; ошибка копирования распространяется без shallow fallback.

## 3. Registry и transformers

```python
class BaseRegistry[T](ABC):
    def __getitem__(self, name: str) -> T: ...


class SimpleRegistry[T](BaseRegistry[T]):
    def __init__(self, entries: Mapping[str, T]) -> None: ...


class BaseTransformerRegistry(BaseRegistry[BaseTransformer]): ...


class BasicTransformerRegistry(
    SimpleRegistry[BaseTransformer], BaseTransformerRegistry
): ...


class BaseTransformer(ABC):
    def transform(
        self, data: PipelineData, *, registry: BaseTransformerRegistry
    ) -> PipelineData: ...


class PipelineTransformer(BaseTransformer):
    def __init__(self, steps: Sequence[str]) -> None: ...
```

Registry копирует mapping готовых объектов и возвращает те же экземпляры по
именам. Неизвестное имя — `KeyError` с именем. Runtime-регистрация, aliases
`get`/`resolve`/`transformer` и хранение результатов запросов не нужны.

Pipeline последовательно разрешает имена и передаёт возвращённый payload
следующему шагу с тем же registry. Вложенный pipeline вызывается так же, как
обычный transformer. Пустые steps возвращают исходный payload. Ошибка прерывает
обработку, последующие шаги не вызываются.

При создании `BasicTransformerRegistry` проверяются отсутствующие ссылки и циклы
зарегистрированных `PipelineTransformer`. Проверка выполняется один раз до
сессии и не анализирует произвольный код leaf transformers.

Явный аргумент registry позволяет выразить композицию без циклического конфига
или mutable bind. Leaf transformer может не использовать его; внешние зависимости
получает в конструкторе. Состояние конкретного запроса в transformer не хранится.

## 4. Интеграция и поиск

```python
class BaseIntegration(ABC): ...


class OpenSearchIntegration(BaseIntegration):
    def __init__(
        self,
        url: str,
        *,
        authorization: str | None = None,
        timeout: float = 30.0,
    ) -> None: ...

    def search(
        self, *, index: str, body: Mapping[str, JSONValue]
    ) -> OpenSearchResult: ...


class BaseOpenSearchDocument(BaseExternalDocument): ...


class VacancyOpenSearchDocument(BaseOpenSearchDocument):
    def __init__(self, hit: Mapping[str, JSONValue]) -> None: ...
    def to_document(self) -> Vacancy: ...


class OpenSearchResult(BaseExternalDocumentCollections):
    def __init__(self, response: Mapping[str, JSONValue]) -> None: ...

    response: Mapping[str, JSONValue]

    def to_document_collections(self) -> SERP: ...


class OpenSearchTransformer(BaseTransformer):
    def __init__(
        self,
        integration: OpenSearchIntegration,
        *,
        index: str,
        fields: Sequence[str],
        size: int,
    ) -> None: ...
```

`url` — корень OpenSearch API, с допустимым префиксом пути прокси, без credentials
в URL, query и fragment. `authorization` — готовое значение заголовка Authorization
(например, Basic или Bearer); `None` означает отсутствие заголовка. Это один
способ передачи авторизации без отдельной иерархии auth modes. Значение берётся
из окружения через Hydra и не записывается в tracked configs, артефакты и логи.

`search` выполняет POST JSON к `url/{index}/_search`. Index кодируется как один
сегмент пути; prefix URL сохраняется. Тело произвольного DSL передаётся без
отбрасывания полей. Использовать `urllib.request`, UTF-8, Content-Type JSON,
явный timeout и стандартную проверку TLS. Каждый response закрывается после
чтения. Redirect отклоняется, чтобы Authorization не ушёл на другой адрес.
Дополнительные запросы ping/healthcheck и retries отсутствуют.

Результат сохраняет стандартный ответ OpenSearch в `response`. При преобразовании
в SERP проверяются структура hits, `_id`, объект `_source` и числовой либо null `_score`.
`timed_out=true` или failed shards означают ошибку неполного ответа.
Пустой `hits.hits` — успешная пустая выдача. Преобразование сохраняет все поля
вакансии и score, но не обещает сохранение всех технических метаданных ответа.
Другой caller может использовать сырой response, например aggregations или
ответ на DSL с `_source=false`, без обязательного преобразования в вакансии.
Это сохраняет полный DSL-контракт integration при более узком контракте SERP.
HTTP/transport/JSON failures сообщаются как `RuntimeError` с безопасным сообщением
и исходной причиной; ошибки условий и преобразования — `ValueError`.

Transformer создаёт `Request(data.filters)`, строит DSL, вызывает integration,
полностью преобразует ответ и только затем заменяет `data.items`. Любая ошибка
до этого присваивания сохраняет прежние items, context и filters.
Для построения DSL достаточно функции в модуле transformer.

Базовый DSL: один `multi_match` с текстом QueryFilter и настроенными fields
в `bool.must`; TermFilter и разрешённые UserPropertyFilter становятся `term`
в `bool.filter`, объединёнными AND. `size` передаётся источнику. `_source`
не ограничивается. Пустую секцию bool.filter можно не включать.

Ноль или несколько QueryFilter, неизвестный тип фильтра и пустой текст после
strip — ошибка до `search`. Пустые index/fields, неположительные size/timeout
и некорректный URL отклоняются при сборке. Term с null отклоняется до поиска:
он не превращается молча в exists или missing. Повторные term-условия остаются
явным AND. Неподдержанный фильтр не игнорируется.

## 5. Эксперимент и отчётность

```python
class BaseArtifact(ABC):
    def to_dict(self) -> dict[str, JSONValue]: ...


class BaseExporter(ABC):
    def export(self, artifact: BaseArtifact) -> None: ...


class JSONExporter(BaseExporter):
    def __init__(self, path: str) -> None: ...


class BaseExperiment(ABC):
    def __init__(self, exporters: Sequence[BaseExporter]) -> None: ...
    def run(self) -> BaseArtifact: ...  # abstract
    def _wrapped_run(self) -> None: ...  # concrete


class SearchExperiment(BaseExperiment):
    def __init__(
        self,
        registry: BaseTransformerRegistry,
        pipeline: str,
        exporters: Sequence[BaseExporter],
        *,
        context: Mapping[str, JSONValue] | None = None,
        filters: Sequence[BaseFilter] = (),
    ) -> None: ...


class SearchArtifact(BaseArtifact):
    def __init__(self, searches: Sequence[Mapping[str, JSONValue]]) -> None: ...
    def to_dict(self) -> dict[str, JSONValue]: ...
```

`_wrapped_run` вызывает `run`, затем exporters в заданном порядке.
Внутри поискового цикла exporters не вызываются. Эксперимент получает зависимости
готовыми. Имя pipeline и отсутствие QueryFilter в статических filters проверяются
до чтения ввода.

Каждый непустой ввод создаёт новый payload с независимым контекстом, настроенными
фильтрами и новым QueryFilter. Предыдущий запрос не редактируется и не накапливается
как условие следующего. Пустой ввод игнорируется без pipeline. Поиск синхронный.
Успех отображается полным JSON и добавляется в артефакт независимым снимком данных.
Конструктор SearchArtifact отделяет вложенные входные данные копированием;
`to_dict` возвращает независимые данные для сериализации.

Формат записи и артефакта:

```json
{
  "searches": [
    {
      "query": "python",
      "items": [
        {"id": "123", "score": 4.2, "fields": {"title": "Python developer"}}
      ]
    }
  ]
}
```

Терминал показывает объект одной записи, exporter сохраняет весь документ.
Порядок searches и items сохраняется. Все поля вакансии доступны внутри fields.
Credentials, контекст пользователя и разрешённый конфиг автоматически не сохраняются.
Это результат конкретной сессии, а не универсальное обещание replay.

EOF завершает с кодом 0 и сохраняет, в том числе `{"searches": []}`.
Ctrl-C завершает с кодом 130 без сохранения; ошибка — с кодом 1 без сохранения.
Специальной команды `exit` нет: эта строка может быть поисковым запросом.
При ошибке второго поиска результаты первого остаются в терминале, но частичный
артефакт не публикуется. Это принятая failure semantics.

JSONExporter сначала сериализует данные и затем атомарно публикует файл
через временный файл в целевом каталоге. При ошибке целевой файл не должен
содержать частичный JSON; временный файл удаляется. Если cleanup не удался,
выдаётся warning с путём оставшегося файла без подмены результата публикации
или исходной ошибки. Каталог результата создаётся
Hydra. Предсуществующий целевой файл не перезаписывается: это ошибка запуска
экспорта, а не разрешение затереть прошлую сессию.

Первый конфиг содержит один exporter. Для общего списка exporters ошибка
останавливает цепочку; транзакция и rollback между несколькими exporters
не обещаются. Ошибка поиска не вызывает exporters вообще.

Верхняя граница запуска логирует фазу и безопасное сообщение, без credentials,
заголовков, тела ответа и сырого transport exception. Причина сохраняется через
exception chaining, но автоматический вывод цепочки с секретами исключается.
Ошибки не подменяются пустой выдачей, повторные catch в каждом шаге не нужны.

## 6. Composition и лаконичность реализации

`src/run.py` остаётся точкой входа: Hydra instantiate одного эксперимента, затем
`_wrapped_run`. Registry получает готовые transformers; integration передаётся
поисковому transformer в конструкторе. Registry передаётся в transform во время
исполнения, поэтому циклические YAML-ссылки не нужны.

Основной конфиг находится в корневом `configs/`; Hydra path от `src/run.py`
указывает туда. Первый эксперимент расположен под
`configs/experiments/vacancy-search/interactive/001-basic.yaml` и выбрать
в defaults основного конфига. `hydra.job.chdir=false`; результат записывается
в `${hydra:runtime.output_dir}/searches.json`. Каталог запуска должен быть уникальным.

Интерполяции связывают настройки индекса, DSL и результата. URL, index, fields
и size обязательны; выдуманных production defaults нет. Авторизация читается
через ссылку на переменную окружения, без материализации секрета в сохранённый
конфиг Hydra. Проверка этого поведения входит в acceptance.

Команды реализации:

```sh
poetry install
poetry run python -m src.run
poetry run ruff check .
poetry run ruff format --check .
poetry run ty check
poetry run pytest
```

Реализация содержит manifest, lockfile и конфигурации quality gates.
Для запуска обязательные настройки задаются через
Hydra overrides/config пользователя. Runtime-зависимость — Hydra; для HTTP,
JSON, копирования и терминального ввода достаточно standard library.

Код размещается у текущих владельцев: domain, core/registry, core/transformers,
integrations/opensearch, components/reporting и experiments/direct. Разбиение
новых файлов — тактическое решение. Термины и абстракции из каркаса сохраняются;
параллельная система Service/Manager/Factory не вводится.

Нужны прямые последовательности действий, dataclasses для данных и функции
для узких stateless transformations. Не требуются Pydantic, Rich, собственный
DI framework, универсальный сериализатор и forwarding wrappers без контракта.
Runtime-проверки сосредоточены у входов и внешнего ответа, а не дублируются
между каждой парой transformers.

## 7. Acceptance evidence

| Проверка | Наблюдаемое доказательство |
| --- | --- |
| Документы/SERP | Вложенные поля и score=None сохранены; порядок hits не меняется |
| Фильтры | Изменение/удаление/замена отклонены; добавление в копии независимо |
| Copy | Shallow разделяет вложенные данные; deep изолирует Vacancy и context, filters остаются общими |
| Registry/pipeline | Identity lookup, порядок и вложенность работают; неизвестные ссылки/циклы отклонены до поиска |
| Ошибка шага | Последующие transformers не вызываются; возвращённый новый payload корректно передаётся дальше |
| DSL | Все три фильтра преобразуются; ошибочные условия не вызывают integration |
| HTTP | Проверены путь с prefix, POST/body, Authorization, timeout, redirect rejection и закрытие response |
| Ответ | Native hits преобразуются; malformed/partial response не становится пустым успехом |
| Сессия | Два ввода дают два независимых поиска; stdout и артефакт сохраняют полные данные |
| Failure | Ошибка второго поиска прекращает чтение и не вызывает exporter; exit codes соответствуют контракту |
| Export | EOF сохраняет; сбой записи не оставляет частичный файл и не затирает существующий |
| Hydra | Реальная композиция создаёт эксперимент и pipeline; секрет отсутствует в артефактах, логах и сохранённых конфигах |
| Прокси | Отдельный реальный smoke search подтверждает работу с разрешённым подключением |

Focused tests используют публичные интерфейсы. Для controlled failures нужен
double интеграции; HTTP проверяется локальным сервером или контролируемым transport
без внешнего индекса. Как минимум один composed test использует настоящий
эксперимент, registry, transformers, преобразование документов и exporter
с временным каталогом. Отдельно тестируется настоящая Hydra-композиция.

Live smoke выполняется отдельно от fixture: в среде локальной приёмки
OpenSearch и доступ к прокси отсутствовали. Проверка внешнего подключения
переносится на машину с доступом и остаётся невыполненной до реального запроса.

Поставка завершена, когда команды проходят, локальным acceptance соответствует
evidence, live smoke явно отмечен как недоступный, документация обновлена по
фактическому интерфейсу, а scaffold остался
только в отложенных направлениях. Заглушка с правильной сигнатурой не является
реализацией. После принятия спеки тактический task plan составляется по этим
контрактам; сама spec не превращается в перечень правок по файлам.
