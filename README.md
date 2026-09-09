# Vinyl Deals Russia

Локальный, консервативный мониторинг предложений на винил. Проект намеренно
предпочитает пропущенное совпадение ложному объединению разных прессингов.

## Быстрый старт

```powershell
python -m pytest
vinyl-deals doctor
vinyl-deals scrape vinyl_ru
vinyl-deals scrape imagine_club --page-limit 1 --enrich
vinyl-deals scrape collectomania --page-limit 1 --enrich
vinyl-deals match --build
vinyl-deals deals --min-class GOOD --limit 50
vinyl-deals search --artist "Opeth" --title "Blackwater Park"
vinyl-deals-gui
vinyl-deals decide-match 12 47 different_release --note "different pressing"
```

Реализованы Vinyl.ru, Imagine Club и Collectomania. `--enrich` читает только
публичные карточки и отключён по умолчанию. При 403/429 источник помечается
как degraded; обхода защиты нет.

Matching проверяет GTIN checksum, отделяет blocking/soft/unknown признаки,
кластеризует только сильные совпадения и сохраняет ручные решения. Manual
`DIFFERENT_RELEASE` является жёстким ограничением: он может безопасно разбить
ошибочный кластер и запрещает merge релизов через такую связь. SQLite включает
foreign keys, канонизирует пары offer id и умеет обновлять legacy-базы.

`--enrich` обрабатывает карточки последовательно с задержкой; ошибка одной
карточки сохраняется как warning, а listing-level offer всё равно попадает в
базу. В CLI выводятся количества найденных, обогащённых и ошибочных карточек.

Telegram, Web UI и новые магазины пока намеренно не реализованы.

Phase 3 adds conservative deal detection for matched releases: market median
uses only fresh (7 days by default), in-stock, same-condition offers and one
price per store. `vinyl-deals deals` explains the market sample, historical
minimum, 30/90-day median, price drop and the deal class. Shipping and
store-sale `old_price` are not used as market-deal evidence.

By default `deals` hides offers without a dependable market sample. Use
`vinyl-deals deals --include-insufficient` to inspect only those with a new
historical low or a price drop of at least 10%; they remain labelled
`INSUFFICIENT`, not as market deals.

`vinyl-deals search` searches matched Releases by artist, title, barcode,
catalogue number, label, year and format. It shows fresh offers from the
supported stores, the absolute lowest price plus separate trusted best new and
best used prices, and a Discogs *search* link; it does not claim a specific
Discogs release match.

## Desktop GUI

`vinyl-deals-gui` opens a native Windows PySide6 window. Search fields use the
same Release Search service as the CLI; selecting a release shows fresh store
offers, with separate visual marks for Lowest price, Best new and Best used.
Use «Обновить данные» to refresh all supported catalogues in the background.
An unavailable store is reported without closing the application or discarding
the other stores' results.
