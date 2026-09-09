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
vinyl-deals decide-match 12 47 different_release --note "different pressing"
```

Реализованы Vinyl.ru, Imagine Club и Collectomania. `--enrich` читает только
публичные карточки и отключён по умолчанию. При 403/429 источник помечается
как degraded; обхода защиты нет.

Matching проверяет GTIN checksum, отделяет blocking/soft/unknown признаки,
кластеризует только сильные совпадения и сохраняет ручные решения. Pricing,
Telegram, Web UI и новые магазины пока намеренно не реализованы.
