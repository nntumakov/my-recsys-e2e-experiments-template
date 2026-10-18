# Сравнение ранжирования без внешних сервисов

Задача: проверить, что произойдёт с выдачей, если к текстовому сигналу добавить
предпочтения по графику и условиям работы. Здесь уже заданы кандидаты и их
признаки. Мы сравниваем их порядок; поиск документов в индексе в пример не входит.

Нужны только зависимости проекта. Все команды выполняются из корня checkout.

```sh
poetry install
poetry run python -m src.run --config-name ranking
```

В `outputs/ranking/<дата>/<run>/comparison.json` появятся полные выдачи девяти
сравнений, NDCG каждого сравнения и сводная статистика. Отдельный каталог на каждый
запуск позволяет сохранить исходный результат, пока меняешь параметры.

## Данные и вопрос эксперимента

[vacancies.yaml](../../configs/ranking/cases/vacancies.yaml) содержит три запроса:

| Case | Запрос | Что может пойти не так |
| --- | --- | --- |
| `warehouse` | комплектовщик без опыта 2/2 | Точное название поднимает опытного комплектовщика; условия поднимают упаковщика |
| `welder` | сварщик вахта с проживанием | Проживание поднимает монтажника и повара, которые не выполняют сварку |
| `driver` | водитель категории C дневные смены | Дневной график поднимает водителя B или CE вместо C |

У каждого запроса пять вымышленных вакансий с полными полями карточки.
`text_match` — условный текстовый сигнал, `preference_match` — условное совпадение
предпочтений. Числа заданы вручную, не вычислены BM25 или нейросетью. QueryFilter
сохраняет исходный запрос для pipeline; в этом примере он не запускает parser.

`relevance` — независимая ручная разметка: 3 означает полное соответствие запросу,
2 — та же профессия с существенным несовпадением условий, 1 — более слабое
соответствие, 0 — другая работа или неподходящая категория прав. Отсутствующая
оценка не равна нулю: метрика завершится ошибкой. Это учебная разметка для
конкретного запроса, а не универсальная таксономия профессий.

## Как собирается обработка

[ranking.yaml](../../configs/ranking.yaml) выбирает данные и эксперимент,
задаёт `k`, `rank_constant` и имена полей сигналов. Вложенный
[002-vacancies.yaml](../../configs/experiments/vacancy-ranking/local/002-vacancies.yaml)
описывает обычный CompareExperiment и registry:

```text
text:       text_score → sort → top
preference: preference_score → sort → top
fused:      branches ┬ text_score → sort       ┐
                    └ preference_score → sort ┴ RRF → top
```

FieldScoreExtractor копирует выбранное поле в score. ScoreSortTransformer
сортирует по убыванию, при равенстве сохраняет порядок входа. TopK оставляет
первые k документов. Эти шаги разделены: сам extractor порядок не меняет.

Ветка RRF получает **все пять** кандидатов каждого сигнала. Если обрезать ветки
до top-3 перед объединением, изменятся голоса и итоговая выдача. Вклад документа
из каждой ветки — `1 / (rank_constant + rank)`, позиции начинаются с единицы.
Складываются голоса рангов, а не scores разных шкал.

CompareExperiment передаёт каждому pipeline независимую копию PipelineData.
BranchTransformer также изолирует ветки. Поэтому перенос одного поля в score
не портит исходные данные и не влияет на следующий вариант.

## Тот же эксперимент из Python

Запусти `poetry run python`, затем:

```python
from pathlib import Path
from hydra import compose, initialize_config_dir
from hydra.utils import instantiate

with initialize_config_dir(
    config_dir=str(Path("configs").resolve()), version_base="1.3"
):
    cfg = compose(
        config_name="ranking",
        overrides=["experiment.exporters=[]"],
    )
    experiment = instantiate(cfg.experiment, _convert_="all")

artifact = experiment.run()
result = artifact.to_dict()
for row in result["results"]:
    ids = [item["id"] for item in row["items"]]
    ndcg = row["metrics"]["ndcg"]["value"]
    print(row["case"], row["pipeline"], ids, round(ndcg, 4))
```

Это тот же Hydra config и те же объекты, что использует CLI. В Compose API
отключён exporter, поскольку Hydra CLI runtime не создаёт каталог запуска.
`run()` возвращает артефакт и не записывает файлы; CLI вызывает exporters после
успеха эксперимента. Готовый исполняемый
[Python-пример](../../src/experiments/examples/ranking.py) делает ту же сборку:

```sh
poetry run python -m src.experiments.examples.ranking
```

Он печатает полный JSON и ничего не сохраняет. Для интерактивного исследования
можно импортировать `compare()` из этого модуля: функция принимает список
обычных Hydra overrides и возвращает ComparisonArtifact.

## Что получилось

Для конфигурации по умолчанию, NDCG@3:

| Case | text | preference | fused |
| --- | ---: | ---: | ---: |
| warehouse | 0.7364 | 0.6299 | 0.7985 |
| welder | 0.9721 | 0.3726 | 0.7985 |
| driver | 1.0000 | 0.7872 | 0.7872 |
| Среднее | 0.9028 | 0.5966 | 0.7947 |

У `warehouse` RRF поднимает `warehouse-trainee` на первую позицию, но на второй
остаётся `packer`. У `driver` дневной курьер B вытесняет ночного водителя C.
RRF объединяет ранги; он не проверяет профессию или обязательность категории прав.
Для этого нужен отдельный contract допуска кандидатов, как в поисковых
экспериментах [006](../specs/006-query-and-painless-candidates.md).

Среднее скрывает улучшение первого запроса и ухудшение остальных. Сначала смотри
`results`: case, pipeline, полные items и отчёт метрики. Затем `summaries`:
count, mean, population stddev, minimum и maximum по трём запросам. Наличие
summary не означает статистически доказанного преимущества.

## Следующий шаг

Поменяй только глубину или RRF constant, сохрани новый результат и сравни ranks:

```sh
poetry run python -m src.run --config-name ranking ranking.k=2
poetry run python -m src.run --config-name ranking ranking.rank_constant=0
```

Из Python те же параметры передаются через compose overrides или
`compare(["ranking.k=2", "ranking.rank_constant=0"])`. Конфигурация связывает k
метрики и TopK одной интерполяцией: отдельный ручной override cutoff не нужен.

Для своего эксперимента добавь Hydra case group с кандидатами, полями признаков
и полной разметкой. Выбери его через `ranking/cases=<name>`. Менять registry
или runner для подстановки данных не требуется.

Ошибка метрики останавливает сравнение до экспорта. JSONExporter публикует файл
атомарно и сохраняет существующий файл при повторном выборе пути. Изменения
`result = artifact.to_dict()` не меняют сохранённый снимок артефакта.

Три придуманных запроса показывают механику эксперимента и типичные ошибки.
Для выводов о поиске нужны реальные кандидаты, независимая разметка и отдельная
оценочная выборка. Подключение OpenSearch, QU и embeddings описано в
[руководстве по внешним сервисам](../usage.md).
