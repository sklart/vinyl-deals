from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QThread, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from vinyl_deals.database.repository import SQLiteRepository
from vinyl_deals.search import ReleaseSearchResult, search_releases
from vinyl_deals.updates import refresh_catalogs

from .workers import UpdateWorker


class MainWindow(QMainWindow):
    """Native Qt shell that delegates search and updates to application services."""

    def __init__(self, database: Path | str = "vinyl_deals.sqlite3", *, search_service: Callable = search_releases, update_service: Callable = refresh_catalogs, url_opener: Callable[[QUrl], bool] = QDesktopServices.openUrl) -> None:
        super().__init__()
        self.repository = SQLiteRepository(database)
        self.search_service = search_service
        self.update_service = update_service
        self.url_opener = url_opener
        self.results: list[ReleaseSearchResult] = []
        self.selected_result: ReleaseSearchResult | None = None
        self._thread: QThread | None = None
        self._worker: UpdateWorker | None = None
        self.setWindowTitle("Vinyl Deals Russia")
        self._build_ui()
        self._restore_window_state()

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
            field.returnPressed.connect(self.perform_search)
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
        self.open_store_button.setEnabled(False)
        self.open_discogs_button.setEnabled(False)
        for button in (self.search_button, self.clear_button, self.refresh_button, self.open_store_button, self.open_discogs_button):
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.release_table = self._table(("Исполнитель", "Альбом", "Label", "Catalog", "Год", "Формат", "Barcode"), "release_table")
        self.offer_table = self._table(("Магазин", "Цена", "Состояние", "Наличие", "Deal"), "offer_table")
        splitter.addWidget(self.release_table)
        splitter.addWidget(self.offer_table)
        splitter.setSizes([280, 320])
        layout.addWidget(splitter, 1)
        self.status_label = QLabel("Введите реквизиты пластинки и нажмите «Найти».")
        self.status_label.setObjectName("status_label")
        layout.addWidget(self.status_label)
        self.setCentralWidget(root)
        self.search_button.clicked.connect(self.perform_search)
        self.clear_button.clicked.connect(self.clear_search)
        self.refresh_button.clicked.connect(self.start_refresh)
        self.release_table.itemSelectionChanged.connect(self.select_release)
        self.offer_table.itemDoubleClicked.connect(lambda _: self.open_selected_offer())
        self.open_store_button.clicked.connect(self.open_selected_offer)
        self.open_discogs_button.clicked.connect(self.open_discogs)

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

    def _restore_window_state(self) -> None:
        geometry = QSettings("VinylDeals", "Desktop").value("window/geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            self.resize(1100, 750)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        QSettings("VinylDeals", "Desktop").setValue("window/geometry", self.saveGeometry())
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
        self.results = self.search_service(self.repository, **criteria)
        self.release_table.setRowCount(0)
        self.offer_table.setRowCount(0)
        self.selected_result = None
        self.open_store_button.setEnabled(False)
        self.open_discogs_button.setEnabled(False)
        for result in self.results:
            row = self.release_table.rowCount()
            self.release_table.insertRow(row)
            values = (result.artist, result.title, result.label or "", result.catalog_number or "", str(result.release_year or ""), result.format or "", result.barcode or "")
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

    def clear_search(self) -> None:
        for field in self.fields.values():
            field.clear()
        self.results = []
        self.selected_result = None
        self.release_table.setRowCount(0)
        self.offer_table.setRowCount(0)
        self.open_store_button.setEnabled(False)
        self.open_discogs_button.setEnabled(False)
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
        self.open_store_button.setEnabled(False)
        self.open_discogs_button.setEnabled(bool(result and result.discogs_url))
        if not result:
            return
        highlights: dict[int, list[str]] = {}
        for name, offer in (("Lowest price", result.lowest_price_offer), ("Best new", result.best_new_offer), ("Best used", result.best_used_offer)):
            if offer:
                highlights.setdefault(offer.offer_id, []).append(name)
        for offer in sorted(result.offers, key=lambda offer: (offer.availability.value != "in_stock", offer.price is None, offer.price or Decimal("0"), offer.store.casefold())):
            row = self.offer_table.rowCount()
            self.offer_table.insertRow(row)
            availability = "В наличии" if offer.availability.value == "in_stock" else "Нет в наличии" if offer.availability.value == "out_of_stock" else "Неизвестно"
            values = (offer.store, f"{offer.price} RUB" if offer.price is not None else "-", offer.condition or "UNKNOWN", availability, offer.deal_class.value if offer.deal_class else "-")
            names = highlights.get(offer.offer_id, [])
            color = QColor("#fff3bf") if "Lowest price" in names else QColor("#d3f9d8") if "Best new" in names else QColor("#d0ebff")
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

    def open_selected_offer(self) -> None:
        selected = self.offer_table.selectedItems()
        if selected:
            self.url_opener(QUrl(selected[0].data(Qt.ItemDataRole.UserRole)))

    def open_discogs(self) -> None:
        if self.selected_result and self.selected_result.discogs_url:
            self.url_opener(QUrl(self.selected_result.discogs_url))

    def start_refresh(self) -> None:
        if self._thread and self._thread.isRunning():
            return
        self.refresh_button.setEnabled(False)
        self.status_label.setText("Обновление данных...")
        self._thread = QThread(self)
        worker = UpdateWorker(self.repository, self.update_service)
        self._worker = worker
        worker.moveToThread(self._thread)
        self._thread.started.connect(worker.run)
        worker.progress.connect(self.status_label.setText)
        worker.completed.connect(self._refresh_completed)
        worker.failed.connect(self._refresh_failed)
        worker.completed.connect(self._thread.quit)
        worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(worker.deleteLater)
        self._thread.finished.connect(self._refresh_finished)
        self._thread.start()

    def _refresh_completed(self, reports: object) -> None:
        self.status_label.setText("Обновление завершено")
        if self.results:
            self.perform_search()

    def _refresh_failed(self, message: str) -> None:
        self.status_label.setText(f"Ошибка обновления: {message}")

    def _refresh_finished(self) -> None:
        self.refresh_button.setEnabled(True)
        self._worker = None
        self._thread = None
