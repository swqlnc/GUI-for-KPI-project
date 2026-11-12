from collections import deque
import csv
from datetime import datetime
import json
import math

import pyqtgraph as pg
import serial
from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QVBoxLayout,
    QWidget,
)
from serial.tools import list_ports

BAUD_RATES = [9600, 57600, 115200]
CHANNEL_NAMES = ("X1", "Y1", "Z1", "X2", "Y2", "Z2")
CHANNEL_COLORS = ("#6fdc8c", "#e0a94a", "#5ea7b8")
DEFAULT_COEFFICIENTS = ((1.0, 0.0),) * len(CHANNEL_NAMES)
MAX_READ_BYTES = 65536

STYLE = """
QMainWindow { background-color: #1b1a17; }
QWidget { color: #d8d3c7; font-family: "Roboto Condensed", sans-serif; font-size: 13px; }
QLabel#fieldLabel { color: #8c887d; font-size: 11px; }
QPushButton {
    background-color: #171614; color: #d8d3c7; border: 1px solid #575249;
    border-radius: 3px; padding: 7px 10px;
}
QPushButton:disabled { color: #6b675f; border-color: #38352f; }
QPushButton:hover { background-color: #27251f; }
QComboBox {
    background-color: #171614; border: 1px solid #38352f; border-radius: 3px;
    padding: 5px 8px; font-family: "JetBrains Mono", monospace; font-size: 12px;
}
QComboBox:disabled { color: #6b675f; }
QPlainTextEdit {
    background-color: #0c1310; color: #d8d3c7;
    border: 1px solid #38352f; border-radius: 6px; padding: 6px;
    font-family: "JetBrains Mono", monospace; font-size: 12px;
}
"""

BTN_DISCONNECTED = """
QPushButton {
    background-color: #171614; border: 1px solid #55d17a; color: #55d17a;
    border-radius: 3px; padding: 7px 18px; font-weight: bold;
}
"""

BTN_CONNECTED = """
QPushButton {
    background-color: #171614; border: 1px solid #e2685a; color: #e2685a;
    border-radius: 3px; padding: 7px 18px; font-weight: bold;
}
"""

LED_OFF = "background-color: #4a4740; border-radius: 5px;"
LED_ON = "background-color: #55d17a; border-radius: 5px;"


class MainWindow(QMainWindow):
    """Головне вікно з прийомом сирих даних COM-порту через таймер."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Прийом даних з COM-порту")
        self.resize(1000, 540)
        self.setStyleSheet(STYLE)
        self._settings = QSettings("SerialMonitor", "SerialMonitor")
        self._coefficients = self._load_coefficients()
        self.connected = False
        self.serial_port = None
        self._record_file = None
        self._csv_writer = None
        self._recorded_rows = 0
        self._record_preview_rows = deque(maxlen=100)
        self._record_preview_dirty = False
        self._record_skip_partial = False
        self.record_dialog = None
        self._plot_buffer = bytearray()
        self._skip_plot_line = False
        self._sample_number = 0
        self._sample_numbers = deque(maxlen=500)
        self._sample_values = {name: deque(maxlen=500) for name in CHANNEL_NAMES}
        self._plots_dirty = False
        self.channel_curves = {}
        self.read_timer = QTimer(self)
        self.read_timer.setInterval(50)
        self.read_timer.timeout.connect(self._read_serial_data)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        root.addLayout(self._build_control_row())
        root.addLayout(self._build_data_views(), stretch=1)

        self._refresh_ports()
        self.render_timer = QTimer(self)
        self.render_timer.setInterval(30)
        self.render_timer.timeout.connect(self._render_plots)
        self.render_timer.start()

    def _build_control_row(self) -> QHBoxLayout:
        row = QHBoxLayout()

        port_col = QVBoxLayout()
        port_label = QLabel("Порт")
        port_label.setObjectName("fieldLabel")
        self.port_combo = QComboBox()
        port_col.addWidget(port_label)
        port_col.addWidget(self.port_combo)
        row.addLayout(port_col)

        self.refresh_btn = QPushButton("Оновити")
        self.refresh_btn.clicked.connect(self._refresh_ports)
        row.addWidget(self.refresh_btn, alignment=Qt.AlignmentFlag.AlignBottom)

        baud_col = QVBoxLayout()
        baud_label = QLabel("Швидкість, бод")
        baud_label.setObjectName("fieldLabel")
        self.baud_combo = QComboBox()
        self.baud_combo.addItems([str(b) for b in BAUD_RATES])
        baud_col.addWidget(baud_label)
        baud_col.addWidget(self.baud_combo)
        row.addLayout(baud_col)

        self.connect_btn = QPushButton("Підключити")
        self.connect_btn.setStyleSheet(BTN_DISCONNECTED)
        self.connect_btn.clicked.connect(self._toggle_connection)
        row.addWidget(self.connect_btn, alignment=Qt.AlignmentFlag.AlignBottom)

        self.record_btn = QPushButton("Запис у CSV")
        self.record_btn.setEnabled(False)
        self.record_btn.clicked.connect(self._toggle_recording)
        row.addWidget(self.record_btn, alignment=Qt.AlignmentFlag.AlignBottom)
        self.preview_btn = QPushButton("Перегляд запису")
        self.preview_btn.clicked.connect(self._show_recording_preview)
        row.addWidget(self.preview_btn, alignment=Qt.AlignmentFlag.AlignBottom)

        self.calibration_btn = QPushButton("Калібрування")
        self.calibration_btn.clicked.connect(self._edit_coefficients)
        row.addWidget(self.calibration_btn, alignment=Qt.AlignmentFlag.AlignBottom)

        row.addStretch()

        status_box = QHBoxLayout()
        self.led = QLabel()
        self.led.setFixedSize(10, 10)
        self.led.setStyleSheet(LED_OFF)
        self.status_label = QLabel("Відключено")
        status_box.addWidget(self.led)
        status_box.addWidget(self.status_label)
        row.addLayout(status_box)

        return row

    def _load_coefficients(self) -> tuple[tuple[float, float], ...]:
        try:
            coefficients = tuple(
                (float(scale), float(offset))
                for scale, offset in json.loads(self._settings.value("calibration/coefficients", "[]"))
            )
        except (TypeError, ValueError, OverflowError):
            return DEFAULT_COEFFICIENTS
        if len(coefficients) != len(CHANNEL_NAMES) or not all(
            math.isfinite(value) for pair in coefficients for value in pair
        ):
            return DEFAULT_COEFFICIENTS
        return coefficients

    def _edit_coefficients(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Паспортні коефіцієнти")
        dialog.setMinimumWidth(530)
        dialog.setStyleSheet("""
            QDialog { background-color: #1b1a17; }
            QTableWidget { background-color: #0c1310; gridline-color: #38352f; }
            QHeaderView::section { background-color: #27251f; color: #d8d3c7; padding: 6px; }
            QLineEdit { background-color: #171614; border: 1px solid #575249; padding: 3px 6px; }
            QLineEdit:focus { border-color: #6fdc8c; }
        """)
        layout = QVBoxLayout(dialog)
        description = QLabel("Вихід = вхід × k + b. Зміни діють з наступного відліку.")
        description.setWordWrap(True)
        layout.addWidget(description)
        table = QTableWidget(len(CHANNEL_NAMES), 2)
        table.setHorizontalHeaderLabels(["Масштаб (k)", "Зміщення нуля (b)"])
        table.setVerticalHeaderLabels(CHANNEL_NAMES)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setMinimumHeight(table.horizontalHeader().sizeHint().height() + sum(
            table.rowHeight(row) for row in range(table.rowCount())
        ) + 4)
        layout.addWidget(table)
        for row, pair in enumerate(self._coefficients):
            for column, value in enumerate(pair):
                field = QLineEdit(repr(value))
                field.setAccessibleName(f"{CHANNEL_NAMES[row]} — {table.horizontalHeaderItem(column).text()}")
                table.setCellWidget(row, column, field)

        error = QLabel()
        error.setWordWrap(True)
        error.setStyleSheet("color: #ff9c8f;")
        layout.addWidget(error)
        buttons = QDialogButtonBox()
        apply_button = buttons.addButton("Застосувати", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Скасувати", QDialogButtonBox.ButtonRole.RejectRole)
        layout.addWidget(buttons)

        def apply_coefficients() -> None:
            coefficients = []
            for row, channel in enumerate(CHANNEL_NAMES):
                pair = []
                for column in range(table.columnCount()):
                    field = table.cellWidget(row, column)
                    try:
                        value = float(field.text().strip().replace(",", "."))
                    except ValueError:
                        value = math.nan
                    if not math.isfinite(value):
                        error.setText(f"{channel}: введіть скінченне число в полі «{table.horizontalHeaderItem(column).text()}».")
                        field.setFocus()
                        field.selectAll()
                        return
                    pair.append(value)
                coefficients.append(tuple(pair))
            self._settings.setValue("calibration/coefficients", json.dumps(coefficients))
            self._settings.sync()
            if self._settings.status() != QSettings.Status.NoError:
                error.setText("Не вдалося зберегти коефіцієнти.")
                return
            self._coefficients = tuple(coefficients)
            dialog.accept()

        apply_button.clicked.connect(apply_coefficients)
        buttons.rejected.connect(dialog.reject)
        dialog.exec()
        dialog.deleteLater()

    def _build_data_views(self) -> QVBoxLayout:
        col = QVBoxLayout()

        charts_row = QHBoxLayout()
        self.channel_plot = self._create_plot("Графік 1", CHANNEL_NAMES[:3])
        self.channel_plot_2 = self._create_plot("Графік 2", CHANNEL_NAMES[3:])
        charts_row.addWidget(self.channel_plot, stretch=1)
        charts_row.addWidget(self.channel_plot_2, stretch=1)
        col.addLayout(charts_row, stretch=3)

        self.data_log = QPlainTextEdit()
        self.data_log.setReadOnly(True)
        self.data_log.setMaximumBlockCount(500)
        self.data_log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.data_log.setMinimumHeight(100)
        # Resizing changes the scroll range even while reception is stopped.
        self.data_log.verticalScrollBar().rangeChanged.connect(self._scroll_log_to_bottom)
        col.addWidget(self.data_log, stretch=1)
        return col

    def _create_plot(self, title: str, names: tuple[str, ...]) -> pg.PlotWidget:
        plot = pg.PlotWidget(background="#0c1310")
        plot.setMinimumHeight(180)
        plot.setTitle(title, color="#d8d3c7")
        plot.setLabel("bottom", "Номер відліку", color="#8c887d")
        plot.setLabel("left", "Значення", color="#8c887d")
        plot.showGrid(x=True, y=True, alpha=0.15)
        plot.enableAutoRange(axis="y", enable=True)
        for name in ("bottom", "left"):
            axis = plot.getAxis(name)
            axis.setPen(pg.mkPen("#8c887d"))
            axis.setTextPen(pg.mkPen("#8c887d"))
        plot.addLegend(labelTextColor="#8c887d", brush="#0c1310", pen="#38352f")
        for name, color in zip(names, CHANNEL_COLORS):
            self.channel_curves[name] = plot.plot(name=name, pen=pg.mkPen(color, width=2))
        return plot

    def _refresh_ports(self) -> None:
        self.port_combo.clear()
        ports = list_ports.comports()
        if ports:
            for p in ports:
                self.port_combo.addItem(p.device)
        else:
            self.port_combo.addItem("Немає портів")

    def _toggle_connection(self) -> None:
        if self.connected:
            self._disconnect_port()
            return

        try:
            self.serial_port = serial.Serial(
                self.port_combo.currentText(),
                int(self.baud_combo.currentText()),
                timeout=0,
            )
        except (serial.SerialException, OSError, ValueError) as exc:
            QMessageBox.warning(self, "Помилка підключення", str(exc))
            return

        self.connected = True
        self._plot_buffer.clear()
        self._skip_plot_line = False
        self._sample_number = 0
        self._sample_numbers.clear()
        for values in self._sample_values.values():
            values.clear()
        for curve in self.channel_curves.values():
            curve.setData([], [])
        self._plots_dirty = False
        self._update_connection_state()
        self.read_timer.start()

    def _disconnect_port(self) -> None:
        self.read_timer.stop()
        self._stop_recording()
        port = self.serial_port
        self.serial_port = None
        self._plot_buffer.clear()
        self._skip_plot_line = False
        self.connected = False
        self._update_connection_state()
        try:
            if port is not None and port.is_open:
                port.close()
        except (serial.SerialException, OSError) as exc:
            QMessageBox.warning(self, "Помилка закриття порту", str(exc))

    def _read_serial_data(self) -> None:
        if self.serial_port is None:
            return
        try:
            # One bounded, nonblocking read per tick: a continuous stream must
            # not keep the GUI in a readline loop or trigger per-line relayouts.
            available = min(self.serial_port.in_waiting, MAX_READ_BYTES)
            if not available:
                return
            data = self.serial_port.read(available)
            if not data:
                return
            cursor = self.data_log.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertText(data.decode("utf-8", errors="replace"))
            self.data_log.setTextCursor(cursor)
            self._scroll_log_to_bottom()

            # Preserve partial lines between ticks and the existing validation.
            parts = data.split(b"\n")
            for line in parts[:-1]:
                self._collect_plot_sample(line + b"\n")
            if parts[-1]:
                self._collect_plot_sample(parts[-1])
            if self._record_file is not None:
                try:
                    self._record_file.flush()
                except OSError as exc:
                    self._recording_error(exc)
        except (serial.SerialException, OSError) as exc:
            self._disconnect_port()
            QMessageBox.warning(self, "Помилка читання порту", str(exc))

    def _scroll_log_to_bottom(self, *_args) -> None:
        scrollbar = self.data_log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _collect_plot_sample(self, chunk: bytes) -> None:
        # Nonblocking reads can split a sample across timer ticks.
        if not self._skip_plot_line:
            self._plot_buffer.extend(chunk)
            if len(self._plot_buffer) > 4096:
                self._plot_buffer.clear()
                self._skip_plot_line = True
        if not chunk.endswith(b"\n"):
            return
        line = bytes(self._plot_buffer)
        self._plot_buffer.clear()
        skip = self._skip_plot_line
        self._skip_plot_line = False
        skip_record = self._record_skip_partial
        self._record_skip_partial = False
        if skip:
            return
        try:
            values = [float(value) for value in line.split()]
        except ValueError:
            return
        if len(values) != 6 or not all(math.isfinite(value) for value in values):
            return
        if self._coefficients != DEFAULT_COEFFICIENTS:
            values = [
                value * scale + offset
                for value, (scale, offset) in zip(values, self._coefficients)
            ]
            if not all(math.isfinite(value) for value in values):
                return
        self._sample_number += 1
        self._sample_numbers.append(self._sample_number)
        for name, value in zip(CHANNEL_NAMES, values):
            self._sample_values[name].append(value)
        self._plots_dirty = True
        if self._csv_writer is not None and not skip_record:
            try:
                self._csv_writer.writerow([self._sample_number, *values])
                self._recorded_rows += 1
                self._record_preview_rows.append([self._sample_number, *values])
                self._record_preview_dirty = True
            except OSError as exc:
                self._recording_error(exc)

    def _toggle_recording(self) -> None:
        if self._record_file is not None:
            self._stop_recording()
            return
        if not self.connected:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Запис даних у CSV",
            datetime.now().strftime("serial_data_%Y%m%d_%H%M%S.csv"),
            "CSV (*.csv)",
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        try:
            self._record_file = open(path, "w", encoding="utf-8-sig", newline="")
            self._csv_writer = csv.writer(self._record_file, delimiter=";")
            self._csv_writer.writerow(["Sample", *CHANNEL_NAMES])
            self._record_file.flush()
        except OSError as exc:
            self._recording_error(exc)
            return
        # Do not include a sample whose first bytes arrived before recording.
        self._record_skip_partial = bool(self._plot_buffer) or self._skip_plot_line
        self.record_btn.setText("Зупинити запис")
        self.record_btn.setStyleSheet(BTN_CONNECTED)
        self.record_btn.setToolTip(f"Запис у файл: {path}")
        self._recorded_rows = 0
        self._record_preview_rows.clear()
        self._record_preview_dirty = True
        self._record_path = path
        self._show_recording_preview()
        self.record_table.setRowCount(0)
        self._update_recording_preview()

    def _show_recording_preview(self) -> None:
        if self.record_dialog is None:
            self.record_dialog = QDialog(self)
            self.record_dialog.setWindowTitle("Перегляд запису CSV")
            self.record_dialog.setStyleSheet("""
                QDialog { background-color: #1b1a17; }
                QTableWidget { background-color: #0c1310; color: #d8d3c7; gridline-color: #38352f; }
                QHeaderView::section { background-color: #27251f; color: #d8d3c7; padding: 6px; }
            """)
            self.record_dialog.resize(780, 420)
            layout = QVBoxLayout(self.record_dialog)
            self.record_info = QLabel()
            self.record_info.setWordWrap(True)
            layout.addWidget(self.record_info)
            self.record_table = QTableWidget(0, 7)
            self.record_table.setHorizontalHeaderLabels(["Відлік", *CHANNEL_NAMES])
            self.record_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            self.record_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            layout.addWidget(self.record_table)
            layout.addWidget(QLabel("Показано останні 100 записів. У CSV зберігаються всі записані відліки."))
        self._update_recording_preview()
        self.record_dialog.show()
        self.record_dialog.raise_()

    def _update_recording_preview(self) -> None:
        if self.record_dialog is None:
            return
        state = "Триває запис" if self._record_file is not None else "Запис зупинено"
        path = getattr(self, "_record_path", "Файл ще не вибрано")
        self.record_info.setText(f"{state} · Рядків: {self._recorded_rows}\n{path}")
        if self._record_preview_dirty:
            self.record_table.setUpdatesEnabled(False)
            self.record_table.setRowCount(len(self._record_preview_rows))
            for row, values in enumerate(self._record_preview_rows):
                for column, value in enumerate(values):
                    self.record_table.setItem(row, column, QTableWidgetItem(str(value)))
            self.record_table.scrollToBottom()
            self.record_table.setUpdatesEnabled(True)
            self._record_preview_dirty = False

    def _stop_recording(self) -> None:
        record_file = self._record_file
        self._record_file = None
        self._csv_writer = None
        self.record_btn.setText("Запис у CSV")
        self.record_btn.setStyleSheet("")
        self.record_btn.setToolTip("")
        if record_file is not None:
            try:
                record_file.close()
            except OSError as exc:
                QMessageBox.warning(self, "Помилка збереження CSV", str(exc))
        self._update_recording_preview()

    def _recording_error(self, exc: OSError) -> None:
        self._stop_recording()
        QMessageBox.warning(self, "Помилка запису CSV", str(exc))

    def _render_plots(self) -> None:
        if self._record_preview_dirty:
            self._update_recording_preview()
        if not self._plots_dirty:
            return
        sample_numbers = list(self._sample_numbers)
        for name, curve in self.channel_curves.items():
            curve.setData(sample_numbers, list(self._sample_values[name]))
        self._plots_dirty = False

    def closeEvent(self, event: QCloseEvent) -> None:
        self._disconnect_port()
        if self.record_dialog is not None:
            self.record_dialog.close()
        super().closeEvent(event)

    def _update_connection_state(self) -> None:
        self.port_combo.setEnabled(not self.connected)
        self.baud_combo.setEnabled(not self.connected)
        self.refresh_btn.setEnabled(not self.connected)
        self.record_btn.setEnabled(self.connected)

        if self.connected:
            self.connect_btn.setText("Відключити")
            self.connect_btn.setStyleSheet(BTN_CONNECTED)
            self.led.setStyleSheet(LED_ON)
            self.status_label.setText(
                f"З'єднано · {self.port_combo.currentText()} · {self.baud_combo.currentText()}"
            )
        else:
            self.connect_btn.setText("Підключити")
            self.connect_btn.setStyleSheet(BTN_DISCONNECTED)
            self.led.setStyleSheet(LED_OFF)
            self.status_label.setText("Відключено")
