# Техническое задание

## Vinyl Deals Russia — мониторинг выгодных предложений на виниловые пластинки

## 1. Цель проекта

Разработать локальное приложение/сервис для автоматического мониторинга цен на виниловые пластинки в российских интернет-магазинах и онлайн-каталогах офлайн-магазинов.

Система должна находить не просто товары со скидкой, а действительно выгодные предложения относительно текущей рыночной цены того же конкретного издания.

Основная задача:

> определить, где конкретный прессинг пластинки сейчас продаётся существенно дешевле обычной рыночной цены.

Отдельный приоритет — магазины Ростова-на-Дону, где возможен самовывоз без стоимости доставки.

---

# 2. Основные возможности

Система должна:

* собирать ассортимент и цены из нескольких магазинов;
* хранить историю цен;
* отслеживать наличие;
* определять конкретное издание пластинки;
* объединять предложения разных магазинов, если они относятся к одному release;
* учитывать различия между прессингами;
* рассчитывать рыночную медианную цену;
* определять размер выгоды;
* отслеживать исторические минимумы;
* поддерживать wishlist;
* отдельно показывать локальные предложения Ростова-на-Дону;
* учитывать стоимость доставки при сравнении;
* поддерживать Telegram-уведомления;
* предоставлять CLI;
* в дальнейшем предоставить простой Web UI.

---

# 3. Главный принцип

Главная ценность проекта — не количество собранных цен, а корректность ответа на вопрос:

> Это действительно одно и то же издание?

Нельзя сравнивать товары только по:

Artist + Album.

Один альбом может существовать как:

* оригинальный прессинг;
* repress;
* remaster;
* юбилейное издание;
* 180 g;
* colored vinyl;
* picture disc;
* mono;
* stereo;
* 1LP;
* 2LP;
* deluxe edition;
* box set;
* RSD edition;
* audiophile edition.

Разные издания не должны автоматически объединяться.

Приоритет проекта:

**precision > recall**

Лучше не объединить два одинаковых релиза, чем ошибочно объединить разные.

---

# 4. Технологии

Основной язык:

Python 3.12+

Рекомендуемый стек:

* httpx;
* selectolax или lxml;
* Pydantic;
* SQLAlchemy;
* SQLite для MVP;
* предусмотреть PostgreSQL;
* FastAPI;
* pytest;
* Playwright только как fallback;
* APScheduler либо аналогичный простой scheduler.

Не использовать тяжёлую инфраструктуру без необходимости.

Не использовать:

* Kubernetes;
* микросервисы;
* Redis;
* Celery;
* сложный React frontend;

на первом этапе.

---

# 5. Архитектура

Пример структуры:

vinyl_deals/
adapters/
base.py
vinyl_ru.py
imagine_club.py
collectomania.py
rio_rostov.py
droog_rostov.py
audiomania.py
respublica.py
drhead.py

```
matching/
    normalize.py
    candidates.py
    matcher.py
    confidence.py

pricing/
    market.py
    history.py
    scoring.py

database/
    models.py
    repository.py
    migrations/

enrichment/
    musicbrainz.py

alerts/
    telegram.py

scheduler/
    jobs.py

web/
    api.py

cli/
    commands.py

config/
    stores.yaml

tests/
    adapters/
    matching/
    pricing/
    fixtures/
```

---

# 6. Источники данных

## Первая очередь — MVP

Реализовывать последовательно:

1. Vinyl.ru
2. Imagine Club
3. Collectomania
4. РИО / R&O, Ростов-на-Дону
5. Друг, Ростов-на-Дону
6. Аудиомания
7. Республика
8. Dr.Head

Не реализовывать все адаптеры одновременно.

После первых трёх магазинов необходимо остановиться и реализовать полноценный release matching.

---

# 7. Вторая очередь

После стабильной работы MVP:

* Vidika;
* Maximum Vinyl;
* Vinylmarkt;
* Vernoshop;
* Tishina;
* AVSound;
* Pult.ru.

Добавлять по одному адаптеру.

---

# 8. Третья очередь

После стабилизации:

* Пластинки-рф;
* Plastinka.com;
* Muzilla;
* ISVOISale;
* другие российские магазины и маркетплейсы.

Маркетплейсы должны иметь отдельную модель продавцов.

Не смешивать обычный магазин и marketplace seller.

---

# 9. Стратегия получения данных

Для каждого магазина перед реализацией адаптера исследовать:

1. официальный API/feed;
2. CSV/XML/JSON-каталог;
3. внутренние публичные JSON/XHR endpoint;
4. server-rendered HTML;
5. только затем Playwright.

Не использовать headless browser, если данные доступны обычными HTTP-запросами.

Не обходить:

* CAPTCHA;
* Cloudflare;
* WAF;
* антибот-защиту;
* авторизацию.

При 403/429 источник должен переходить в degraded state.

---

# 10. Vinyl.ru

Реализовать первым.

Перед обходом HTML проверить возможность использования публичной выгрузки полного каталога.

Карточки товаров использовать только для данных, отсутствующих в каталоге.

Источник:

source = vinyl_ru

---

# 11. Imagine Club

Реализовать вторым.

Особое внимание:

* barcode/EAN;
* label;
* country;
* year;
* format;
* edition metadata.

Источник:

source = imagine_club

---

# 12. Collectomania

Реализовать третьим.

Особенно собирать:

* barcode;
* catalog number;
* label;
* country;
* year;
* vinyl color;
* edition tags.

Источник:

source = collectomania

---

# 13. РИО / R&O

Магазин:

РИО / R&O
Ростов-на-Дону

Источник:

source = rio_rostov

Особые признаки:

city = "Ростов-на-Дону"
local_store = true
pickup_available = true

Реализовать отдельный адаптер:

adapters/rio_rostov.py

Исследовать:

* каталог;
* пагинацию;
* внутренние API/XHR;
* карточки товаров;
* цены;
* скидки;
* наличие;
* состояние;
* лейбл;
* год;
* страну;
* каталожный номер;
* barcode;
* формат;
* признаки издания.

Новый и винтажный винил учитывать отдельно.

---

# 14. Магазин «Друг»

Магазин:

Друг
Ростов-на-Дону

Источник:

source = droog_rostov

Особые признаки:

city = "Ростов-на-Дону"
local_store = true
pickup_available = true

Перед написанием адаптера определить устойчивый публичный источник ассортимента:

1. собственный сайт;
2. официальный интернет-каталог;
3. официальные страницы;
4. публичные объявления магазина;
5. иные официально используемые магазином площадки.

Не использовать сторонний агрегатор как единственный источник без проверки происхождения данных.

Особенно важно различать:

NEW
USED

Для USED собирать:

condition_media
condition_sleeve

---

# 15. Унифицированный интерфейс магазина

Создать:

BaseStoreAdapter

Логический API:

get_catalog()

get_changed_products()

get_product()

normalize_product()

get_stock()

опционально:

get_city_stock()

Каждый адаптер возвращает RawOffer.

Логика определения выгодности и release matching не должна находиться внутри адаптера магазина.

---

# 16. RawOffer

Минимальные поля:

source
source_product_id
url
fetched_at

artist_raw
artist_normalized

title_raw
title_normalized

edition_raw

price
old_price
currency

availability
stock_quantity
stock_text

city

local_store
pickup_available
delivery_available

condition_media
condition_sleeve

format
vinyl_size
rpm
disc_count

label

catalog_number_raw
catalog_number_normalized

barcode

release_year
country

vinyl_color

edition_tags

description

image_url

raw_data

Исходные значения никогда не уничтожать.

---

# 17. Release

Создать отдельную сущность Release.

Release — конкретное издание пластинки.

Поля:

id

artist
title

barcode

label
catalog_number

release_year
country

format
disc_count
vinyl_size
rpm

vinyl_color

edition_tags

musicbrainz_release_id

created_at
updated_at

Один Release может иметь много Offer.

---

# 18. Release matching

## Уровень 1

Совпадает barcode/EAN/UPC.

Дополнительно проверить отсутствие конфликтов:

artist
title
format
disc_count

Confidence:

0.98–1.00

---

## Уровень 2

Совпадают:

catalog_number + label

при совпадении:

artist
title
format

Confidence:

0.93–0.98

---

## Уровень 3

Сравнивать:

artist
title
label
release_year
country
format
disc_count
vinyl_color
edition_tags

Confidence рассчитывать по весам.

---

## Уровень 4

Совпадает только:

artist + title

Не объединять автоматически.

Создавать:

possible_match

---

# 19. Отрицательные признаки matching

Запрещать автоматическое объединение при конфликтах:

* разные barcode;
* разные catalog number при одинаковом label;
* 1LP vs 2LP;
* LP vs 7";
* 33 RPM vs 45 RPM;
* black vs colored edition;
* standard vs picture disc;
* standard vs deluxe;
* standard vs box set;
* mono vs stereo;
* обычное издание vs RSD;
* обычное издание vs audiophile pressing;
* сильно различающиеся годы переиздания.

---

# 20. Нормализация

Нормализовать:

* Unicode;
* регистр;
* пробелы;
* тире;
* кавычки;
* ampersand;
* catalog number;
* варианты LP / 2 LP / 2LP.

Не удалять признаки:

remaster
remastered
mono
stereo
deluxe
limited
anniversary
colored
colour
picture disc
180g
45 RPM
RSD
box
box set
audiophile

---

# 21. Состояние винила

Минимальная шкала:

NEW_SEALED
MINT
NM
EX
VG_PLUS
VG
GOOD
UNKNOWN

Новый и б/у винил не сравнивать как один рынок.

Для USED хранить отдельно:

condition_media
condition_sleeve

---

# 22. История цен

Цена не должна перезаписываться.

Таблица:

PriceHistory

offer_id
observed_at
price
old_price
availability

Расчёты:

current_price
previous_price
first_seen_price
minimum_30d
minimum_90d
median_30d
median_90d

---

# 23. Рыночная цена

Для каждого Release собрать актуальные предложения.

Использовать только:

* товары в наличии;
* сопоставимое состояние;
* достаточно свежие данные;
* достаточно высокий matching confidence.

Основной показатель:

market_median

Медиана предпочтительнее среднего.

При расчёте рынка для конкретного предложения желательно исключать само это предложение.

---

# 24. Effective price

Сравнивать не только цену товара.

Формула:

effective_price =
product_price

* delivery_cost

- unconditional_discount

На первом этапе стоимость доставки может быть неизвестной.

Не учитывать автоматически:

* бонусы;
* cashback;
* персональные скидки;
* банковские акции;
* индивидуальные промокоды.

---

# 25. Локальные предложения Ростова-на-Дону

Ростовские магазины должны иметь дополнительный приоритет.

При бесплатном самовывозе:

effective_price = product_price

Это позволяет корректно сравнить:

магазин Москва:
3800 ₽ + 700 ₽ доставка = 4500 ₽

магазин Ростов:
4190 ₽ + 0 ₽ = 4190 ₽

В этом случае ростовское предложение выгоднее.

---

# 26. Market discount

Рассчитывать:

market_discount =
1 - effective_price / market_median

Пример:

цена = 3990 ₽
рынок = 5990 ₽

скидка относительно рынка ≈ 33 %

---

# 27. Deal classification

Первоначальные категории:

NORMAL
< 10 %

INTERESTING
10–15 %

GOOD
15–25 %

HOT
25–35 %

VERY_HOT

> = 35 %

HOT и VERY_HOT разрешать только при достаточно высоком confidence.

Рекомендация:

match_confidence >= 0.90

---

# 28. Минимальное количество источников

3 и более предложений:

полноценная рыночная оценка.

2 предложения:

показывать с предупреждением о малой выборке.

1 предложение:

не считать полноценной оценкой рынка.

В этом случае использовать историю собственной цены.

---

# 29. Историческая выгодность

Рассчитывать:

historical_discount_30d
historical_discount_90d

Событие:

HISTORICAL_LOW

Даже если конкурентных предложений мало, исторический минимум должен считаться полезным сигналом.

---

# 30. Типы событий

Общие:

PRICE_DROP
HISTORICAL_LOW
NEW_STOCK
RESTOCK

Локальные:

LOCAL_NEW_STOCK
LOCAL_PRICE_DROP
LOCAL_HISTORICAL_LOW

---

# 31. Wishlist

Поддержать:

## Artist watch

Например:

Pink Floyd

## Album watch

Pink Floyd — Animals

## Release watch

Конкретный pressing по:

barcode
catalog_number
release_id

---

# 32. Пользовательские фильтры

Поддерживать:

maximum_price

minimum_market_discount

new_only

used_only

exclude_picture_disc

exclude_7inch

exclude_boxsets

allowed_formats

excluded_labels

minimum_match_confidence

city

local_only

pickup_only

---

# 33. CLI

Минимальные команды:

vinyl-deals scrape vinyl_ru

vinyl-deals scrape rio_rostov

vinyl-deals scrape all

vinyl-deals match

vinyl-deals deals

vinyl-deals deals --min-discount 25

vinyl-deals deals --local

vinyl-deals deals --city "Ростов-на-Дону"

vinyl-deals deals --pickup

vinyl-deals local

vinyl-deals history <release-id>

vinyl-deals doctor

---

# 34. Команда local

Команда:

vinyl-deals local

должна выводить лучшие предложения Ростова-на-Дону.

Пример:

LOCAL DEALS — Ростов-на-Дону

1. Pink Floyd — Animals
   РИО
   3 990 ₽
   рынок: 5 290 ₽
   −24.6 %

2. The Cure — Disintegration
   Друг
   4 500 ₽
   рынок: 5 700 ₽
   −21.1 %

---

# 35. Telegram

После стабилизации matching реализовать Telegram-уведомления.

Пример:

🔥 VERY HOT — 32 %

Depeche Mode — Violator

Label:
Catalog:
Barcode:
Year:
Country:
Format:

Цена:
3 990 ₽

Медиана рынка:
5 850 ₽

90d minimum:
4 790 ₽

Предложений:
5

Match confidence:
98 %

Магазин:
...

---

Локальный вариант:

📍 РОСТОВ — 🔥 PRICE DROP

The Cure — ...

Магазин:
РИО

Цена:
3 490 ₽

Рынок:
4 990 ₽

Разница:
−30 %

Самовывоз:
да

---

# 36. Possible Matches

Создать отдельную очередь спорных совпадений.

Пользователь должен иметь возможность выбрать:

CONFIRM SAME RELEASE

CONFIRM DIFFERENT RELEASE

IGNORE

Ручные решения сохранять.

---

# 37. Matching regression corpus

Создать вручную размеченный набор минимум:

100–200 пар товаров.

Категории:

SAME_RELEASE
DIFFERENT_RELEASE
UNCERTAIN

Обязательно включить:

* разные цвета;
* 1LP/2LP;
* EU/US;
* разные годы;
* разные catalog number;
* одинаковый barcode;
* remaster vs standard;
* picture disc;
* неполные карточки.

Использовать corpus для regression tests.

---

# 38. Scraper tests

Каждый адаптер должен иметь offline fixtures:

listing
product
sale
out_of_stock
missing_metadata

Parser unit tests не должны зависеть от live-сайта.

---

# 39. Контроль качества crawler

Создать:

ScrapeRun

Поля:

store
started_at
finished_at
status
pages_processed
offers_found
offers_changed
errors
warnings

Если вчера было 5000 товаров, а сегодня parser нашёл 12:

не считать, что товары исчезли.

Статус:

PARSER_SUSPECTED_BROKEN

---

# 40. Incremental crawling

Не перекачивать весь каталог при каждом запуске.

Хранить:

source_product_id
last_seen
last_checked
content_hash

По возможности мониторить:

* новые поступления;
* распродажу;
* изменения цены;
* изменения наличия.

Полный обход делать реже.

---

# 41. Ограничение нагрузки

Для каждого сайта:

rate_limit
concurrency
timeout
retry_after
exponential_backoff

При HTTP:

403
429

снижать частоту либо временно отключать источник.

Не пытаться обходить защиту.

---

# 42. Database

Минимальные таблицы:

stores
store_locations

raw_products
offers

releases
release_matches

offer_stock

price_history

watchlist

alerts

scrape_runs

manual_match_decisions

---

# 43. Индексы

Создать индексы минимум на:

barcode

catalog_number

artist_normalized

title_normalized

release_id

source_product_id

fetched_at

---

# 44. MusicBrainz

После появления стабильного matching добавить необязательное enrichment через MusicBrainz.

Использовать для:

* barcode verification;
* release metadata;
* альтернативных названий;
* поиска release ID.

Не использовать как источник российских цен.

Все запросы кэшировать.

---

# 45. Web UI

После CLI.

Минимальные страницы:

Deals

Local Deals

Wishlist

Releases

Offers

Price History

Possible Matches

Stores

Scrape Status

---

# 46. Deals UI

Сортировки:

* discount;
* price;
* artist;
* date found;
* match confidence.

Фильтры:

* store;
* city;
* local;
* pickup;
* artist;
* price;
* discount;
* condition;
* format.

---

# 47. Объяснимость результата

Каждый Deal должен показывать:

Почему предложение считается выгодным:

Current price:
3 490 ₽

Market median:
5 190 ₽

Market discount:
−32.8 %

90d median:
4 990 ₽

90d minimum:
3 790 ₽

Comparable stores:
5

Match confidence:
98 %

Не использовать непрозрачный ML score как основной показатель.

---

# 48. Первый этап разработки

## Phase 0 — Foundation

Реализовать:

* project skeleton;
* models;
* SQLite;
* CLI;
* BaseStoreAdapter;
* logging;
* tests;
* fixtures.

---

# 49. Phase 1 — первые источники

Последовательно реализовать:

1. Vinyl.ru
2. Imagine Club
3. Collectomania

Получить реальные данные в локальной БД.

---

# 50. Phase 2 — Matching

После трёх магазинов прекратить добавление источников.

Реализовать:

* normalization;
* candidate generation;
* barcode matching;
* catalog matching;
* weighted matching;
* confidence;
* possible matches;
* manual decisions;
* regression corpus.

Не масштабировать crawler, пока matching не станет достаточно надёжным.

---

# 51. Phase 3 — Pricing

Реализовать:

* history;
* market median;
* historical median;
* historical minimum;
* deal classification;
* price drop;
* historical low.

---

# 52. Phase 4 — Ростов

Добавить:

4. РИО
5. Друг

Реализовать:

* local_store;
* city;
* pickup;
* effective price;
* local deals;
* локальные уведомления.

---

# 53. Phase 5 — расширение MVP

Добавить:

6. Аудиоманию
7. Республику
8. Dr.Head

После этого MVP должен охватывать:

* крупные федеральные магазины;
* профильные магазины;
* локальный Ростов.

---

# 54. Phase 6 — пользовательские функции

Реализовать:

* wishlist;
* Telegram;
* scheduler;
* Local Deals;
* Web UI;
* фильтры.

---

# 55. Phase 7 — расширение

Добавлять остальные магазины по одному.

Перед каждым новым адаптером:

1. исследовать сайт;
2. выбрать легитимный источник данных;
3. сохранить fixtures;
4. написать parser;
5. написать tests;
6. подключить к crawler.

---

# 56. Что не делать на первом этапе

Не реализовывать сразу:

* AI/LLM matching;
* ML;
* OCR;
* мобильное приложение;
* сложный frontend;
* автопокупку;
* обход CAPTCHA;
* scraping 30 магазинов;
* маркетплейсы;
* Discogs как обязательную зависимость;
* сложную распределённую инфраструктуру.

---

# 57. Acceptance Criteria MVP

MVP считать готовым, когда:

1. Работают минимум 8 источников.

2. Среди них есть:

   * Vinyl.ru;
   * Imagine Club;
   * Collectomania;
   * РИО;
   * Друг;
   * Аудиомания;
   * Республика;
   * Dr.Head.

3. Для каждого адаптера существуют offline parser tests.

4. История цен сохраняется.

5. Система отслеживает наличие.

6. Exact barcode matching работает.

7. Catalog number + label matching работает.

8. Учитываются:

   * год;
   * страна;
   * формат;
   * disc count;
   * vinyl color;
   * edition tags.

9. Новый и б/у винил не смешиваются.

10. Есть минимум 100 размеченных regression cases.

11. Существуют Possible Matches.

12. Ошибочное объединение разных pressings считается критической ошибкой.

13. Deal определяется относительно рыночной медианы.

14. Система показывает объяснение каждого Deal.

15. Есть исторические минимумы.

16. Есть локальный режим Ростова-на-Дону.

17. Самовывоз учитывается при сравнении effective price.

18. Поломка одного parser не останавливает crawler.

19. 403/429 корректно обрабатываются.

20. Система может работать по расписанию.

21. Работает команда:

vinyl-deals deals

22. Работает команда:

vinyl-deals local

---

# 58. Первый рабочий milestone

Минимально полезная версия должна позволять выполнить:

vinyl-deals deals

и получить:

HOT 31.7 %

Artist:
Album:
Release:

Label:
Catalog number:
Barcode:

Store:
...

Current:
3 990 ₽

Market median:
5 840 ₽

Compared stores:
4

90d median:
4 990 ₽

90d minimum:
4 390 ₽

Match confidence:
0.98

URL:
...

Если данных недостаточно:

POSSIBLE DEAL

Insufficient release identification

а не выдавать ложный HOT.

---

# 59. Порядок работы Codex

Не реализовывать проект одним большим коммитом.

В корне репозитория сохранить это ТЗ как:

SPEC.md

Начать только с:

Phase 0

и

Vinyl.ru.

После каждого этапа:

1. запускать тесты;
2. проверять реальные данные;
3. предоставлять краткий отчёт;
4. фиксировать обнаруженные ограничения;
5. не переходить к следующему этапу при критических проблемах с качеством данных.

Формат отчёта Codex после каждого этапа:

* что реализовано;
* какие файлы изменены;
* какие тесты добавлены;
* какие реальные данные получены;
* какие ограничения магазина обнаружены;
* какие спорные решения приняты;
* результаты тестов;
* что остаётся до следующего milestone.

---

# 60. Итоговый принцип

Проект должен быть не «парсером скидок», а системой оценки рынка конкретных виниловых изданий.

Правильный порядок разработки:

сбор данных
→ нормализация
→ определение release
→ сравнение цен
→ история
→ локальные предложения
→ уведомления
→ расширение количества магазинов.

Количество магазинов вторично.

Качество определения конкретного прессинга — главный критерий качества системы.
