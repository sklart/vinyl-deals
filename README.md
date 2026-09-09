# Vinyl Deals Russia

Локальный, консервативный мониторинг предложений на винил. На текущем этапе
готовы фундамент проекта и offline-парсер публичной CSV-выгрузки Vinyl.ru.

## Быстрый старт

```powershell
python -m pytest
vinyl-deals doctor
vinyl-deals scrape vinyl_ru
```

`scrape` читает публичную выгрузку только обычным HTTP-запросом. При 403/429
источник помечается как degraded и не предпринимается попыток обхода защиты.
