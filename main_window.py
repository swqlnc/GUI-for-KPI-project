from collections import deque
import math

import pyqtgraph as pg
import serial
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from serial.tools import list_ports

BAUD_RATES = [9600, 57600, 115200]

STYLE = """
QMainWindow { background-color: #1b1a17; }
QWidget { color: #d8d3c7; font-family: "Roboto Condensed", sans-serif; font-size: 13px; }
QLabel#fieldLabel { color: #8c887d; font-size: 11px; }
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

PLACEHOLDER_STYLE = """
QFrame { border: 1px dashed #38352f; border-radius: 6px; }
QLabel { color: #8c887d; }
"""


class MainWindow(QMainWindow):
    """Головне вікно з прийомом сирих даних COM-порту через таймер."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Прийом даних з COM-порту")
        self.resize(680, 420)
        self.setStyleSheet(STYLE)
        self.connected = False
        self.serial_port = None
        self._plot_buffer = bytearray()
        self._skip_plot_line = False
        self._sample_number = 0
        self._sample_numbers = deque(maxlen=500)
        self._sample_values = deque(maxlen=500)
        self.read_timer = QTimer(self)
        self.read_timer.setInterval(50)
        self.read_timer.timeout.connect(self._read_serial_data)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        root.addLayout(self._build_control_row())
        root.addLayout(self._build_placeholders())
        root.addStretch()

        self._refresh_ports()

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

    def _build_placeholders(self) -> QVBoxLayout:
        col = QVBoxLayout()

        charts_row = QHBoxLayout()
        self.channel_plot = pg.PlotWidget(background="#0c1310")
        self.channel_plot.setMinimumHeight(180)
        self.channel_plot.setTitle("Канал 1 · x1", color="#d8d3c7")
        self.channel_plot.setLabel("bottom", "Номер відліку")
        self.channel_plot.setLabel("left", "Значення")
        self.channel_plot.showGrid(x=True, y=True, alpha=0.15)
        for name in ("bottom", "left"):
            axis = self.channel_plot.getAxis(name)
            axis.setPen(pg.mkPen("#8c887d"))
            axis.setTextPen(pg.mkPen("#d8d3c7"))
        self.channel_curve = self.channel_plot.plot(pen=pg.mkPen("#55d17a", width=2))
        charts_row.addWidget(self.channel_plot)
        charts_row.addWidget(self._placeholder_frame("Графік 2", min_height=140))
        col.addLayout(charts_row)

        self.data_log = QPlainTextEdit()
        self.data_log.setReadOnly(True)
        self.data_log.setMaximumBlockCount(500)
        self.data_log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.data_log.setMinimumHeight(100)
        col.addWidget(self.data_log)
        return col

    @staticmethod
    def _placeholder_frame(title: str, min_height: int) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(PLACEHOLDER_STYLE)
        frame.setMinimumHeight(min_height)
        layout = QVBoxLayout(frame)
        label = QLabel(title)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        return frame

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
        self._sample_values.clear()
        self.channel_curve.setData([], [])
        self._update_connection_state()
        self.read_timer.start()

    def _disconnect_port(self) -> None:
        self.read_timer.stop()
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
            while self.serial_port.in_waiting > 0:
                raw_line = self.serial_port.readline()
                if not raw_line:
                    break
                # Insert without adding a newline: timeout=0 can return a partial line.
                cursor = self.data_log.textCursor()
                cursor.movePosition(QTextCursor.MoveOperation.End)
                cursor.insertText(raw_line.decode("utf-8", errors="replace"))
                self.data_log.setTextCursor(cursor)
                self.data_log.ensureCursorVisible()
                self._collect_plot_sample(raw_line)
            self.channel_curve.setData(
                list(self._sample_numbers), list(self._sample_values)
            )
        except (serial.SerialException, OSError) as exc:
            self._disconnect_port()
            QMessageBox.warning(self, "Помилка читання порту", str(exc))

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
        if skip:
            return
        try:
            values = [float(value) for value in line.split()]
        except ValueError:
            return
        if len(values) != 6 or not all(math.isfinite(value) for value in values):
            return
        self._sample_number += 1
        self._sample_numbers.append(self._sample_number)
        self._sample_values.append(values[0])

    def closeEvent(self, event: QCloseEvent) -> None:
        self._disconnect_port()
        super().closeEvent(event)

    def _update_connection_state(self) -> None:
        self.port_combo.setEnabled(not self.connected)
        self.baud_combo.setEnabled(not self.connected)
        self.refresh_btn.setEnabled(not self.connected)

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
