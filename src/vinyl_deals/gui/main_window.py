from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from vinyl_deals.database.repository import SQLiteRepository
from vinyl_deals.discogs import DiscogsApiClient, DiscogsService
from vinyl_deals.build_metadata import metadata as build_metadata
from vinyl_deals.domain import StoreSearchQuery
from vinyl_deals.live_search import live_search
from vinyl_deals.runtime import application_database_path, settings_path
from vinyl_deals.scheduler import ALLOWED_INTERVALS, Scheduler
from vinyl_deals.search import ReleaseSearchResult, search_releases
from vinyl_deals.updates import refresh_catalogs
from vinyl_deals.updates import STORE_LABELS

from .workers import LiveSearchWorker, TaskWorker, UpdateWorker


class MainWindow(QMainWindow):
    """Native Qt shell that delegates search and updates to application services."""

    def __init__(self, database: Path | str | None = None, *, search_service: Callable = search_releases, live_search_service: Callable = live_search, update_service: Callable = refresh_catalogs, url_opener: Callable[[QUrl], bool] = QDesktopServices.openUrl, scheduler_factory: Callable = Scheduler, discogs_service_factory: Callable[[SQLiteRepository], DiscogsService] | None = None) -> None:
        super().__init__()
        database_path = Path(database) if database is not None else application_database_path()
        self.repository = SQLiteRepository(database_path)
        self.settings = QSettings(str(settings_path(database_path)), QSettings.Format.IniFormat)
        self.search_service = search_service
        self.live_search_service = live_search_service
        self.update_service = update_service
        self.url_opener = url_opener
        self.results: list[ReleaseSearchResult] = []
        self.selected_result: ReleaseSearchResult | None = None
        self._worker: UpdateWorker | None = None
        self._live_worker: LiveSearchWorker | None = None
        self._live_store_status: dict[str, str] = {}
        self._live_store_details: dict[str, str] = {}
        self._alert_worker: TaskWorker | None = None
        self._discogs_worker: TaskWorker | None = None
        self._discogs_pending: tuple[int, tuple[int, ...]] | None = None
        self._search_generation = 0
        self.discogs_service_factory = discogs_service_factory or (lambda repository: DiscogsService(repository, DiscogsApiClient(token=str(self.settings.value("discogs/token", "") or "") or None)))
        self._search_performed = False
        self._updating = False
        self._closing = False
        self.scheduler = scheduler_factory(self.repository, live_search_service=live_search_service, parent=self)
        self.scheduler.start_guard = self._scheduler_start_allowed
        self.setWindowTitle("Vinyl Deals Russia")
        self._build_ui()
        self._restore_window_state()
        self._restore_scheduler()

    def _build_ui(self) -> None:
        root = QWidget(self)
        layout = QVBoxLayout(root)
        form = QGridLayout()
        self.fields: dict[str, QLineEdit] = {}
        field_specs = (
            ("artist", "Исполнитель"), ("title", "Альбом"), ("barcode", "Barcode"), ("catalog", "Каталожный номер"),
            ("label", "Label"), ("year", "Год"), ("format", "Формат"),
        )
        for index, (key, label) in enumerate(field_specs):
            field = QLineEdit()
            field.setObjectName(f"{key}_field")
            field.setPlaceholderText(label)
            field.returnPressed.connect(self.start_live_search)
            self.fields[key] = field
            form.addWidget(QLabel(label), index // 4 * 2, index % 4 * 2)
            form.addWidget(field, index // 4 * 2, index % 4 * 2 + 1)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        self.search_button = QPushButton("Найти")
        self.clear_button = QPushButton("Очистить")
        self.refresh_button = QPushButton("Обновить данные")
        self.open_store_button = QPushButton("Открыть магазин")
        self.open_discogs_button = QPushButton("Открыть Discogs")
        self.confirm_discogs_button = QPushButton("Подтвердить Discogs")
        self.about_button = QPushButton("О программе")
        self.open_store_button.setEnabled(False)
        self.open_discogs_button.setEnabled(False)
        self.confirm_discogs_button.setEnabled(False)
        for button in (self.search_button, self.clear_button, self.refresh_button, self.open_store_button, self.open_discogs_button, self.confirm_discogs_button, self.about_button):
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.release_table = self._table(("Исполнитель", "Альбом", "Label", "Catalog", "Год", "Формат", "Barcode", "Лучшая цена", "Лучшая итоговая", "Медиана", "Deal %", "Предложений", "Магазинов", "Совпадение", "Discogs"), "release_table")
        self.offer_table = self._table(("Магазин", "Цена", "Итоговая цена", "Самовывоз", "Состояние", "Наличие", "Deal %", "Класс"), "offer_table")
        splitter.addWidget(self.release_table)
        offers_panel = QWidget()
        offers_layout = QVBoxLayout(offers_panel)
        offers_layout.setContentsMargins(0, 0, 0, 0)
        self.release_summary = QLabel("Выберите Release, чтобы увидеть сводку цен.")
        self.release_summary.setObjectName("release_summary")
        self.release_summary.setWordWrap(True)
        offers_layout.addWidget(self.release_summary)
        offers_layout.addWidget(self.offer_table, 1)
        splitter.addWidget(offers_panel)
        splitter.setSizes([280, 320])
        layout.addWidget(splitter, 1)
        self.discogs_attribution = QLabel("Discogs data provided by Discogs.")
        self.discogs_attribution.setObjectName("discogs_attribution")
        layout.addWidget(self.discogs_attribution)
        self.status_label = QLabel("Введите реквизиты пластинки и нажмите «Найти».")
        self.status_label.setObjectName("status_label")
        layout.addWidget(self.status_label)
        self.search_page = root
        self.tabs = QTabWidget()
        self.tabs.addTab(root, "Поиск")
        self.tabs.addTab(self._build_tracking_page(), "Отслеживание")
        self.setCentralWidget(self.tabs)
        self.search_button.clicked.connect(self.start_live_search)
        self.clear_button.clicked.connect(self.clear_search)
        self.refresh_button.clicked.connect(self.start_refresh)
        self.release_table.itemSelectionChanged.connect(self.select_release)
        self.offer_table.itemSelectionChanged.connect(self._update_open_actions)
        self.offer_table.itemDoubleClicked.connect(lambda _: self.open_selected_offer())
        self.open_store_button.clicked.connect(self.open_selected_offer)
        self.open_discogs_button.clicked.connect(self.open_discogs)
        self.confirm_discogs_button.clicked.connect(self.confirm_discogs_candidate)
        self.about_button.clicked.connect(self.show_about)

    def _build_tracking_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page)
        controls = QHBoxLayout()
        self.watch_add_button = QPushButton("Добавить выбранный Release")
        self.watch_remove_button = QPushButton("Удалить")
        self.watch_enable_button = QPushButton("Включить / отключить")
        self.watch_edit_button = QPushButton("Редактировать параметры")
        self.watch_open_button = QPushButton("Открыть предложения")
        for button in (self.watch_add_button, self.watch_remove_button, self.watch_enable_button, self.watch_edit_button, self.watch_open_button): controls.addWidget(button)
        layout.addLayout(controls)
        self.watch_table = self._table(("Исполнитель", "Альбом", "Вкл.", "Макс. цена", "Min deal", "Local", "Город", "Самовывоз", "Последний alert", "Последняя проверка", "Статус", "Предложений"), "watch_table")
        layout.addWidget(self.watch_table, 1)
        scheduler_controls = QHBoxLayout()
        self.scheduler_enabled = QCheckBox("Автопроверка")
        self.scheduler_interval = QComboBox(); self.scheduler_interval.addItems([str(value) for value in ALLOWED_INTERVALS])
        self.scheduler_auto_send = QCheckBox("Автоотправка Telegram")
        self.autostart_enabled = QCheckBox("Автозапуск Windows (не поддерживается portable-сборкой)")
        self.autostart_enabled.setVisible(False)
        self.scheduler_run_button = QPushButton("Проверить сейчас")
        for widget in (self.scheduler_enabled, QLabel("Интервал (мин):"), self.scheduler_interval, self.scheduler_auto_send, self.autostart_enabled, self.scheduler_run_button): scheduler_controls.addWidget(widget)
        scheduler_controls.addStretch(); layout.addLayout(scheduler_controls)
        alert_controls = QHBoxLayout(); self.alert_open_store_button = QPushButton("Открыть магазин"); self.alert_open_discogs_button = QPushButton("Открыть Discogs"); self.alert_send_button = QPushButton("Отправить pending alerts")
        for button in (self.alert_open_store_button, self.alert_open_discogs_button, self.alert_send_button): alert_controls.addWidget(button)
        layout.addLayout(alert_controls)
        self.alert_table = self._table(("Дата", "Событие", "Релиз", "Магазин", "Цена", "Статус"), "alert_table")
        layout.addWidget(self.alert_table, 1)
        self.watch_add_button.clicked.connect(self.add_selected_watch)
        self.watch_remove_button.clicked.connect(self.remove_selected_watch)
        self.watch_enable_button.clicked.connect(self.toggle_selected_watch)
        self.watch_edit_button.clicked.connect(self.edit_selected_watch)
        self.watch_open_button.clicked.connect(self.open_watch_offers)
        self.scheduler_enabled.toggled.connect(self.save_scheduler_settings)
        self.scheduler_interval.currentTextChanged.connect(self.save_scheduler_settings)
        self.scheduler_auto_send.toggled.connect(self.save_scheduler_settings)
        self.scheduler_run_button.clicked.connect(self.run_scheduler_cycle)
        self.alert_table.itemSelectionChanged.connect(self._update_alert_actions)
        self.alert_open_store_button.clicked.connect(self.open_alert_store)
        self.alert_open_discogs_button.clicked.connect(self.open_alert_discogs)
        self.alert_send_button.clicked.connect(self.send_pending_alerts)
        self.refresh_tracking(); self._update_alert_actions()
        return page

    @staticmethod
    def _table(headers: tuple[str, ...], name: str) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setObjectName(name)
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        return table

    def refresh_tracking(self) -> None:
        self.watch_table.setRowCount(0)
        for entry in self.repository.watchlist_entries():
            row = self.watch_table.rowCount(); self.watch_table.insertRow(row)
            values = (entry["artist"], entry["title"], "Да" if entry["enabled"] else "Нет", entry["max_price"] or "", entry["min_deal_class"] or "GOOD", "Да" if entry["local_only"] else "Нет", entry["city"] or "", "Да" if entry["pickup_only"] else "Нет", self._last_alert_text(int(entry["release_id"])), entry["last_checked_at"] or "—", entry["last_check_status"] or "—", entry["last_offer_count"])
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value)); item.setData(Qt.ItemDataRole.UserRole, entry["release_id"]); self.watch_table.setItem(row, column, item)
        self.alert_table.setRowCount(0)
        for alert in self.repository.alerts():
            payload = alert["payload"]; row = self.alert_table.rowCount(); self.alert_table.insertRow(row)
            status = "sent" if alert["sent_at"] else "error" if alert["send_error"] else "pending"
            values = (str(alert["created_at"]), alert["event_type"], f"{payload.get('artist', '-')} — {payload.get('title', '-')}", payload.get("store", "-"), payload.get("price", "-"), status)
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value)); item.setData(Qt.ItemDataRole.UserRole, alert); self.alert_table.setItem(row, column, item)
        self._update_alert_actions()

    def _last_alert_text(self, release_id: int) -> str:
        rows = [row for row in self.repository.alerts() if row["release_id"] == release_id]
        return str(rows[-1]["event_type"]) if rows else "—"

    def _selected_watch_id(self) -> int | None:
        items = self.watch_table.selectedItems(); return int(items[0].data(Qt.ItemDataRole.UserRole)) if items else None

    def add_selected_watch(self) -> None:
        if not self.selected_result:
            self.status_label.setText("Сначала выберите Release в поиске."); return
        self.repository.add_watchlist(self.selected_result.release_id); self.refresh_tracking()

    def remove_selected_watch(self) -> None:
        if (release_id := self._selected_watch_id()) is not None: self.repository.remove_watchlist(release_id); self.refresh_tracking()

    def toggle_selected_watch(self) -> None:
        if (release_id := self._selected_watch_id()) is None: return
        entry = next(row for row in self.repository.watchlist_entries() if row["release_id"] == release_id)
        self.repository.set_watchlist_enabled(release_id, not bool(entry["enabled"])); self.refresh_tracking()

    def edit_selected_watch(self) -> None:
        if (release_id := self._selected_watch_id()) is None: return
        entry = next(row for row in self.repository.watchlist_entries() if row["release_id"] == release_id)
        dialog = QDialog(self); dialog.setWindowTitle("Параметры отслеживания"); form = QFormLayout(dialog)
        maximum = QLineEdit(str(entry["max_price"] or "")); minimum = QComboBox(); minimum.addItems(["", "NORMAL", "INTERESTING", "GOOD", "HOT", "VERY_HOT"]); minimum.setCurrentText(str(entry["min_deal_class"] or ""))
        local = QCheckBox(); local.setChecked(bool(entry["local_only"])); city = QLineEdit(str(entry["city"] or "")); pickup = QCheckBox(); pickup.setChecked(bool(entry["pickup_only"]))
        for label, widget in (("Макс. цена", maximum), ("Min deal class", minimum), ("Только local", local), ("Город", city), ("Только самовывоз", pickup)): form.addRow(label, widget)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel); buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); form.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                value = Decimal(maximum.text()) if maximum.text().strip() else None
            except Exception:
                self.status_label.setText("Макс. цена должна быть числом.")
                return
            self.update_watch_parameters(release_id, max_price=value, min_deal_class=minimum.currentText() or None, local_only=local.isChecked(), city=city.text().strip() or None, pickup_only=pickup.isChecked())

    def update_watch_parameters(self, release_id: int, *, max_price: Decimal | None, min_deal_class: str | None, local_only: bool, city: str | None, pickup_only: bool) -> None:
        """Persist GUI editing through the existing watchlist repository API."""
        entry = next(row for row in self.repository.watchlist_entries() if row["release_id"] == release_id)
        self.repository.add_watchlist(release_id, max_price=max_price, min_deal_class=min_deal_class, local_only=local_only, city=city, pickup_only=pickup_only)
        if not entry["enabled"]:
            self.repository.set_watchlist_enabled(release_id, False)
        self.refresh_tracking()

    def open_watch_offers(self) -> None:
        if (release_id := self._selected_watch_id()) is None: return
        self.results = [result for result in self.search_service(self.repository) if result.release_id == release_id]
        self.release_table.setRowCount(0)
        if self.results:
            result = self.results[0]; self.release_table.insertRow(0)
            for column, value in enumerate(self._release_row_values(result)):
                item = QTableWidgetItem(value); item.setData(Qt.ItemDataRole.UserRole, result.release_id if column == 0 else None); self.release_table.setItem(0, column, item)
            self.tabs.setCurrentWidget(self.search_page); self.release_table.selectRow(0)

    def _update_alert_actions(self) -> None:
        items = self.alert_table.selectedItems() if hasattr(self, "alert_table") else []
        payload = items[0].data(Qt.ItemDataRole.UserRole)["payload"] if items else {}
        if hasattr(self, "alert_open_store_button"):
            self.alert_open_store_button.setEnabled(bool(payload.get("url"))); self.alert_open_discogs_button.setEnabled(bool(payload.get("discogs_url")))

    def _selected_alert_payload(self) -> dict[str, object] | None:
        items = self.alert_table.selectedItems(); return items[0].data(Qt.ItemDataRole.UserRole)["payload"] if items else None

    def open_alert_store(self) -> None:
        if (payload := self._selected_alert_payload()) and payload.get("url"): self.url_opener(QUrl(str(payload["url"])))

    def open_alert_discogs(self) -> None:
        if (payload := self._selected_alert_payload()) and payload.get("discogs_url"): self.url_opener(QUrl(str(payload["discogs_url"])))

    def _restore_scheduler(self) -> None:
        settings = self.settings; enabled = settings.value("scheduler/enabled", False, type=bool); interval = settings.value("scheduler/interval", 60, type=int); interval = interval if interval in ALLOWED_INTERVALS else 60
        auto_send = settings.value("scheduler/auto_send", False, type=bool)
        # Do not let each restored widget write a half-restored value back to
        # the INI file through its change signal.
        for widget in (self.scheduler_enabled, self.scheduler_interval, self.scheduler_auto_send):
            widget.blockSignals(True)
        self.scheduler_enabled.setChecked(enabled)
        self.scheduler_interval.setCurrentText(str(interval))
        self.scheduler_auto_send.setChecked(auto_send)
        for widget in (self.scheduler_enabled, self.scheduler_interval, self.scheduler_auto_send):
            widget.blockSignals(False)
        self.scheduler.configure(enabled=enabled, interval_minutes=interval, auto_send=auto_send)
        self.scheduler.status.connect(self.status_label.setText); self.scheduler.cycle_completed.connect(self._scheduler_completed); self.scheduler.cycle_failed.connect(lambda message: self.status_label.setText(f"Ошибка проверки: {message}")); self.scheduler.running_changed.connect(self._set_updating)

    def save_scheduler_settings(self) -> None:
        if not hasattr(self, "scheduler"): return
        enabled, interval, auto_send = self.scheduler_enabled.isChecked(), int(self.scheduler_interval.currentText()), self.scheduler_auto_send.isChecked()
        settings = self.settings; settings.setValue("scheduler/enabled", enabled); settings.setValue("scheduler/interval", interval); settings.setValue("scheduler/auto_send", auto_send); settings.sync(); self.scheduler.configure(enabled=enabled, interval_minutes=interval, auto_send=auto_send)
        if enabled and not self._updating:
            self.status_label.setText(f"Следующая проверка: через {interval} мин.")

    def show_about(self) -> None:
        details = build_metadata()
        QMessageBox.information(self, "О Vinyl Deals", f"Vinyl Deals Russia\nВерсия: {details.get('version', 'unknown')}\nСборка: {details.get('build_date', 'unknown')}\nCommit: {details.get('commit', 'unknown')}\n\nDiscogs data provided by Discogs.")

    def run_scheduler_cycle(self) -> None:
        if self._scheduler_start_allowed() and self.scheduler.trigger(): self.status_label.setText("Проверка watchlist...")

    def _scheduler_completed(self, result: object) -> None:
        count = result.get("alerts", 0) if isinstance(result, dict) else 0
        checked = result.get("checked", 0) if isinstance(result, dict) else 0
        partial = result.get("partial", 0) if isinstance(result, dict) else 0
        status = f"Проверено: {checked}; новых alerts: {count}"
        if partial:
            status += f"; частичных: {partial}"
        if self.scheduler.enabled:
            status += f". Следующая проверка: через {self.scheduler.interval_minutes} мин."
        self.status_label.setText(status); self.refresh_tracking()
        if self._search_performed:
            self.perform_search()

    def send_pending_alerts(self) -> None:
        from vinyl_deals.alert_delivery import deliver_pending_alerts
        if not self._alert_send_allowed():
            return
        def send() -> int:
            return deliver_pending_alerts(self.repository)
        self._set_updating(True)
        self._alert_worker = TaskWorker(send)
        self._alert_worker.completed.connect(self._alert_delivery_completed)
        self._alert_worker.failed.connect(lambda message: self.status_label.setText(f"Ошибка Telegram: {message}"))
        self._alert_worker.finished.connect(self._alert_delivery_finished)
        self._alert_worker.start()

    def _alert_delivery_completed(self, result: object) -> None:
        sent = getattr(result, "sent", 0); failed = getattr(result, "failed", 0)
        self.status_label.setText(f"Telegram sent: {sent}, failed: {failed}")
        self.refresh_tracking()

    def _alert_delivery_finished(self) -> None:
        worker = self._alert_worker
        self._alert_worker = None
        if worker:
            worker.deleteLater()
        self._set_updating(False)

    def _maintenance_busy(self) -> bool:
        return bool(
            self._closing
            or (self._worker and self._worker.isRunning())
            or (self._live_worker and self._live_worker.isRunning())
            or self.scheduler.running
            or (self._alert_worker and self._alert_worker.isRunning())
        )

    def _scheduler_start_allowed(self) -> bool:
        return not self._closing and not (self._worker and self._worker.isRunning()) and not (self._live_worker and self._live_worker.isRunning()) and not (self._alert_worker and self._alert_worker.isRunning())

    def _alert_send_allowed(self) -> bool:
        return not self._maintenance_busy()

    def _restore_window_state(self) -> None:
        geometry = self.settings.value("window/geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            self.resize(1100, 750)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.sync()
        self._closing = True
        self.scheduler.shutdown()
        if self._worker and self._worker.isRunning():
            self._worker.wait()
        if self._live_worker and self._live_worker.isRunning():
            self._live_worker.wait()
        worker = self._alert_worker
        if worker and worker.isRunning():
            worker.wait()
        worker = self._discogs_worker
        if worker and worker.isRunning():
            worker.wait()
        super().closeEvent(event)

    def _criteria(self) -> dict[str, object] | None:
        criteria: dict[str, object] = {key: field.text().strip() or None for key, field in self.fields.items()}
        if criteria["year"]:
            try:
                criteria["year"] = int(str(criteria["year"]))
            except ValueError:
                self.status_label.setText("Год должен быть числом.")
                return None
        return criteria

    def perform_search(self) -> None:
        criteria = self._criteria()
        if criteria is None:
            return
        self._search_performed = True
        self.results = self.search_service(self.repository, **criteria)
        self._render_search_results()

    def _render_search_results(self) -> None:
        self.release_table.setRowCount(0)
        self.offer_table.setRowCount(0)
        self.selected_result = None
        self.release_summary.setText("Выберите Release, чтобы увидеть сводку цен.")
        self._update_open_actions()
        for result in self.results:
            row = self.release_table.rowCount()
            self.release_table.insertRow(row)
            discogs = result.discogs_confidence or "поиск"
            values = self._release_row_values(result, discogs)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, result.release_id)
                self.release_table.setItem(row, column, item)
        if not self.results:
            self.status_label.setText("Ничего не найдено")
        else:
            self.status_label.setText(f"Найдено релизов: {len(self.results)}")
            self.release_table.selectRow(0)

    def start_live_search(self) -> None:
        criteria = self._criteria()
        if criteria is None:
            return
        query = StoreSearchQuery(
            artist=str(criteria["artist"]) if criteria["artist"] else None,
            title=str(criteria["title"]) if criteria["title"] else None,
            barcode=str(criteria["barcode"]) if criteria["barcode"] else None,
            catalog_number=str(criteria["catalog"]) if criteria["catalog"] else None,
        )
        if query.is_empty():
            self.status_label.setText("Введите исполнителя и альбом, штрихкод или каталожный номер.")
            return
        if self._maintenance_busy():
            return
        self._search_performed = True
        self._search_generation += 1
        self._live_store_status = {}
        self._live_store_details = {}
        self._set_updating(True)
        self.status_label.setText("Поиск во всех магазинах...")
        worker = LiveSearchWorker(self.repository, query, self.live_search_service)
        self._live_worker = worker
        worker.progress.connect(self._live_store_finished)
        worker.completed.connect(self._live_search_completed)
        worker.failed.connect(self._live_search_failed)
        worker.finished.connect(self._live_search_finished)
        worker.start()

    def _live_store_finished(self, result: object) -> None:
        source = str(getattr(result, "source", "магазин"))
        label = STORE_LABELS.get(source, source)
        offers = int(getattr(result, "offers", 0))
        kind = str(getattr(result, "status_kind", "error"))
        marker = {
            "found": f"✓ {offers}", "empty": "0 результатов", "cached": f"↻ Кэш {offers}",
            "unsupported": "ⓘ Live-search не поддерживается", "restricted": "⚠ Доступ ограничен",
            "timeout": "⌛ Таймаут", "error": "✕ Ошибка",
        }.get(kind, "✕ Ошибка")
        self._live_store_status[source] = f"{label}: {marker}"
        detail = str(getattr(result, "detail", ""))
        if detail:
            self._live_store_details[source] = detail
        releases = tuple(getattr(result, "releases", ()))
        if releases:
            self.results = list(releases)
            self._render_search_results()
        self.status_label.setText("Поиск во всех магазинах:\n" + "\n".join(self._live_store_status.values()))
        self.status_label.setToolTip("\n".join(f"{STORE_LABELS.get(source, source)}: {detail}" for source, detail in self._live_store_details.items()))

    def _live_search_completed(self, result: object) -> None:
        self.results = list(getattr(result, "releases", ()))
        self._render_search_results()
        possible = len(getattr(result, "possible_matches", ()))
        suffix = f"; возможных совпадений: {possible}" if possible else ""
        self.status_label.setText(f"Найдено релизов: {len(self.results)}{suffix}")
        self._start_discogs_enrichment(tuple(item.release_id for item in self.results))

    def _start_discogs_enrichment(self, release_ids: tuple[int, ...]) -> None:
        """Store prices remain visible while the optional API work happens later."""
        if not release_ids or self._closing:
            return
        generation = self._search_generation
        if self._discogs_worker and self._discogs_worker.isRunning():
            self._discogs_pending = (generation, release_ids)
            return
        self._launch_discogs_worker(generation, release_ids)

    def _launch_discogs_worker(self, generation: int, release_ids: tuple[int, ...]) -> None:
        service = self.discogs_service_factory(self.repository)
        if not service.enabled:
            self.status_label.setText(f"Найдено релизов: {len(self.results)}. Discogs: настройте DISCOGS_TOKEN или discogs/token в data/settings.ini.")
            return
        def enrich() -> tuple[int, list[object], str | None]:
            candidates = [service.enrich_release(release_id) for release_id in release_ids]
            return generation, candidates, service.last_error
        self._discogs_worker = TaskWorker(enrich)
        self._discogs_worker.completed.connect(self._discogs_completed)
        self._discogs_worker.finished.connect(self._discogs_finished)
        self._discogs_worker.start()

    def _discogs_completed(self, result: object) -> None:
        generation = result[0] if isinstance(result, tuple) else -1
        if generation != self._search_generation:
            return
        if isinstance(result, tuple) and len(result) > 2 and result[2] == "auth":
            self.status_label.setText("Discogs: неверный или недействительный token.")
            return
        criteria = self._criteria()
        if criteria is not None:
            refreshed = self.search_service(self.repository, **criteria)
            # A custom/injected live-search service may expose partial rows
            # before persistence; never make those rows disappear merely
            # because the secondary metadata worker completed.
            if refreshed:
                self.results = refreshed
                self._render_search_results()

    def _discogs_finished(self) -> None:
        worker = self._discogs_worker
        self._discogs_worker = None
        if worker:
            worker.deleteLater()
        pending = self._discogs_pending
        self._discogs_pending = None
        if pending and not self._closing:
            self._launch_discogs_worker(*pending)

    def _live_search_failed(self, message: str) -> None:
        self.status_label.setText(f"Ошибка live-поиска: {message}")

    def _live_search_finished(self) -> None:
        worker = self._live_worker
        self._live_worker = None
        if worker:
            worker.deleteLater()
        self._set_updating(False)

    def clear_search(self) -> None:
        for field in self.fields.values():
            field.clear()
        self.results = []
        self.selected_result = None
        self._search_performed = False
        self.release_table.setRowCount(0)
        self.offer_table.setRowCount(0)
        self._update_open_actions()
        self.status_label.setText("Введите реквизиты пластинки и нажмите «Найти».")

    def select_release(self) -> None:
        selected = self.release_table.selectedItems()
        if not selected:
            return
        release_id = selected[0].data(Qt.ItemDataRole.UserRole)
        self.selected_result = next((result for result in self.results if result.release_id == release_id), None)
        self.populate_offers()

    def populate_offers(self) -> None:
        self.offer_table.setRowCount(0)
        result = self.selected_result
        self._update_open_actions()
        if not result:
            self.release_summary.setText("Выберите Release, чтобы увидеть сводку цен.")
            return
        self.release_summary.setText(self._summary_text(result))
        highlights: dict[int, list[str]] = {}
        for name, offer in (("Lowest price", result.lowest_price_offer), ("Best effective", result.best_effective_offer), ("Best new", result.best_new_offer), ("Best used", result.best_used_offer)):
            if offer:
                highlights.setdefault(offer.offer_id, []).append(name)
        for offer in sorted(result.offers, key=lambda offer: (offer.availability.value != "in_stock", offer.price is None, offer.price or Decimal("0"), offer.store.casefold())):
            row = self.offer_table.rowCount()
            self.offer_table.insertRow(row)
            availability = "В наличии" if offer.availability.value == "in_stock" else "Нет в наличии" if offer.availability.value == "out_of_stock" else "Неизвестно"
            store_label = STORE_LABELS.get(offer.store, offer.store)
            store = f"📍 {store_label}" if offer.local_store else store_label
            effective = f"{offer.effective_price} RUB" if offer.effective_price_known and offer.effective_price is not None else "?"
            pickup = "Да" if offer.pickup_available else "Нет"
            discount = f"{offer.discount_pct:.0f}%" if offer.discount_pct is not None else "-"
            condition = "Неизвестно" if not offer.condition or offer.condition.upper() == "UNKNOWN" else offer.condition
            deal_class = "Недостаточно данных" if offer.deal_class and offer.deal_class.value == "INSUFFICIENT" else offer.deal_class.value if offer.deal_class else "—"
            values = (store, f"{offer.price} RUB" if offer.price is not None else "-", effective, pickup, condition, availability, discount, deal_class)
            names = highlights.get(offer.offer_id, [])
            color = QColor("#fff3bf") if "Lowest price" in names else QColor("#d3f9d8") if "Best effective" in names or "Best new" in names else QColor("#d0ebff")
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, offer.url)
                if names:
                    item.setBackground(color)
                    item.setToolTip("; ".join(names))
                    font = QFont(item.font())
                    font.setBold(True)
                    item.setFont(font)
                self.offer_table.setItem(row, column, item)
        if not result.offers:
            self.status_label.setText("Нет актуальных предложений")

    @staticmethod
    def _money(value: Decimal | None) -> str:
        return f"{value} ₽" if value is not None else "—"

    def _release_row_values(self, result: ReleaseSearchResult, discogs: str | None = None) -> tuple[str, ...]:
        discogs_value = discogs if discogs is not None else result.discogs_confidence or "поиск"
        return (
            result.artist, result.title, result.label or "", result.catalog_number or "",
            str(result.release_year or ""), result.format or "", result.barcode or "",
            self._money(result.lowest_price_offer.price if result.lowest_price_offer else None),
            self._money(result.best_effective_offer.effective_price if result.best_effective_offer and result.best_effective_offer.effective_price_known else None),
            self._money(result.market_median),
            f"{result.discount_pct:.1f}%" if result.discount_pct is not None else "—",
            str(result.offer_count), str(result.store_count),
            self._release_match_status(result),
            discogs_value,
        )

    @staticmethod
    def _release_match_status(result: ReleaseSearchResult) -> str:
        if result.discogs_confidence:
            return "Подтверждено"
        if result.has_possible_matches:
            return "Возможное совпадение"
        # Multiple shops linked by a validated GTIN or catalogue+label are
        # strong store evidence.  A lone/sparse result is deliberately not.
        if result.offer_count >= 2 and (result.barcode or (result.catalog_number and result.label)):
            return "Подтверждено"
        return "Не подтверждено / одиночное предложение"

    def _summary_text(self, result: ReleaseSearchResult) -> str:
        best = result.lowest_price_offer
        best_text = f"{self._money(best.price)} · {STORE_LABELS.get(best.store, best.store)}" if best else "—"
        effective = self._money(result.best_effective_offer.effective_price) if result.best_effective_offer and result.best_effective_offer.effective_price_known else "—"
        if result.market_median is None or result.discount_pct is None:
            assessment = "Медиана: — · Выгода: — · Оценка: недостаточно данных"
        else:
            deal = result.deal_class.value if result.deal_class else "—"
            assessment = f"Медиана: {self._money(result.market_median)} · Выгода: {result.discount_pct:.1f}% · {deal}"
        return f"Лучшая цена: {best_text}    Лучшая итоговая: {effective}\n{assessment}\nПредложений: {result.offer_count} · Магазинов: {result.store_count} · Сравнений: {result.comparable_count}"

    def _update_open_actions(self) -> None:
        selected = self.offer_table.selectedItems()
        url = selected[0].data(Qt.ItemDataRole.UserRole) if selected else None
        self.open_store_button.setEnabled(not self._updating and bool(url) and QUrl(str(url)).isValid())
        self.open_discogs_button.setEnabled(not self._updating and bool(self.selected_result and self.selected_result.discogs_url))
        possible = self.repository.discogs_matches(self.selected_result.release_id) if self.selected_result else []
        self.confirm_discogs_button.setEnabled(not self._updating and any(row["status"] == "possible" for row in possible))

    def _set_updating(self, updating: bool) -> None:
        self._updating = updating
        if updating and self.scheduler.timer.isActive():
            self.scheduler.timer.stop()
            self._resume_scheduler_timer = True
        elif not updating and getattr(self, "_resume_scheduler_timer", False) and not self._closing and not self.scheduler.shutting_down:
            self.scheduler.timer.start(self.scheduler.interval_minutes * 60_000)
            self._resume_scheduler_timer = False
        for field in self.fields.values():
            field.setEnabled(not updating)
        for control in (self.search_button, self.clear_button, self.refresh_button, self.release_table, self.offer_table, self.confirm_discogs_button):
            control.setEnabled(not updating)
        for control in (self.watch_add_button, self.watch_remove_button, self.watch_enable_button, self.watch_edit_button, self.watch_open_button, self.watch_table, self.scheduler_enabled, self.scheduler_interval, self.scheduler_auto_send, self.autostart_enabled, self.scheduler_run_button, self.alert_table, self.alert_open_store_button, self.alert_open_discogs_button, self.alert_send_button):
            control.setEnabled(not updating)
        self._update_open_actions()

    def open_selected_offer(self) -> None:
        selected = self.offer_table.selectedItems()
        if selected and (url := QUrl(str(selected[0].data(Qt.ItemDataRole.UserRole)))).isValid():
            self.url_opener(url)

    def open_discogs(self) -> None:
        if self.selected_result and self.selected_result.discogs_url:
            self.url_opener(QUrl(self.selected_result.discogs_url))

    def confirm_discogs_candidate(self) -> None:
        if not self.selected_result:
            return
        choices = [row for row in self.repository.discogs_matches(self.selected_result.release_id) if row["status"] == "possible"]
        labels = [
            "#{} — {} — {} / {} / {} / {} / {}".format(
                row["discogs_release_id"], row["metadata"].get("artist", "?"), row["metadata"].get("title", "?"),
                row["metadata"].get("release_year", "?"), row["metadata"].get("country", "?"),
                row["metadata"].get("label", "?"), row["metadata"].get("catalog_number", "?"),
            )
            for row in choices
        ]
        selected, accepted = QInputDialog.getItem(self, "Подтвердить Discogs", "Конкретный релиз:", labels, 0, False)
        if not accepted:
            return
        index = labels.index(selected)
        candidate = choices[index]
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Проверка Discogs кандидата")
        metadata = candidate["metadata"]
        dialog.setText("#{}\n{} — {}\n{} / {}\n{} / {}\nBarcode: {}\nФормат: {}".format(
            candidate["discogs_release_id"], metadata.get("artist", "?"), metadata.get("title", "?"),
            metadata.get("release_year", "?"), metadata.get("country", "?"), metadata.get("label", "?"),
            metadata.get("catalog_number", "?"), metadata.get("barcode", "?"), metadata.get("format", "?"),
        ))
        open_button = dialog.addButton("Открыть Discogs", QMessageBox.ButtonRole.ActionRole)
        confirm_button = dialog.addButton("Подтвердить", QMessageBox.ButtonRole.AcceptRole)
        dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.exec()
        if dialog.clickedButton() is open_button:
            self.url_opener(QUrl(str(candidate["discogs_url"])))
            return
        if dialog.clickedButton() is not confirm_button:
            return
        self.repository.confirm_discogs_match(self.selected_result.release_id, int(candidate["discogs_release_id"]))
        self.perform_search()

    def start_refresh(self) -> None:
        if self._maintenance_busy():
            return
        self._set_updating(True)
        self.status_label.setText("Обновление данных...")
        worker = UpdateWorker(self.repository, self.update_service)
        self._worker = worker
        worker.progress.connect(self.status_label.setText)
        worker.completed.connect(self._refresh_completed)
        worker.failed.connect(self._refresh_failed)
        worker.finished.connect(self._refresh_finished)
        worker.start()

    def _refresh_completed(self, reports: object) -> None:
        self.status_label.setText("Обновление завершено")
        if self._search_performed:
            self.perform_search()

    def _refresh_failed(self, message: str) -> None:
        self.status_label.setText(f"Ошибка обновления: {message}")

    def _refresh_finished(self) -> None:
        self._set_updating(False)
        if self._worker:
            self._worker.deleteLater()
        self._worker = None
