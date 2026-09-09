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
uses in-stock, same-condition offers and one price per store; historical price
signals come from `price_history`. Shipping and store-sale `old_price` are not
used as market-deal evidence.
