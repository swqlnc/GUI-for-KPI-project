import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch
from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from main_window import MainWindow, CHANNEL_NAMES, MAX_READ_BYTES


class BufferedPort:
    def __init__(self):
        self.data = bytearray()
        self.read_sizes = []
        self.is_open = True

    @property
    def in_waiting(self):
        return len(self.data)

    def read(self, size):
        self.read_sizes.append(size)
        result = bytes(self.data[:size])
        del self.data[:size]
        return result

    def close(self):
        self.is_open = False


class SerialGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()
        self.port = BufferedPort()
        self.window.serial_port = self.port

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def receive(self, data):
        self.port.data.extend(data)
        self.window._read_serial_data()

    def test_batch_uses_one_read_and_one_log_update(self):
        changes = []
        self.window.data_log.textChanged.connect(lambda: changes.append(True))
        self.receive(b'1 2 3 4 5 6\r\n' * 100)
        self.assertEqual(len(self.port.read_sizes), 1)
        self.assertEqual(len(changes), 1)
        self.assertEqual(self.window._sample_number, 100)
        self.assertEqual(len(self.window.data_log.toPlainText().splitlines()), 100)
        self.window._render_plots()
        for name in CHANNEL_NAMES:
            self.assertEqual(len(self.window.channel_curves[name].getData()[0]), 100)

    def test_partial_invalid_and_oversized_lines(self):
        self.receive(b'1 2 3')
        self.assertEqual(self.window._sample_number, 0)
        self.receive(b' 4 5 6\r')
        self.receive(b'\n1 2 3 4 5\n1 2 3 4 5 6 7\nNaN 2 3 4 5 6\n1 inf 3 4 5 6\ngarbage\n')
        self.receive(b' ' * 4097)
        self.receive(b'\n7 8 9 10 11 12\n')
        self.assertEqual(self.window._sample_number, 2)
        self.assertEqual(list(self.window._sample_values['X1']), [1, 7])
        self.assertEqual(list(self.window._sample_values['Z2']), [6, 12])
        self.assertIn('garbage', self.window.data_log.toPlainText())

    def test_read_budget_and_history_limit(self):
        line = b'1 2 3 4 5 6\n'
        count = MAX_READ_BYTES // len(line) + 100
        self.receive(line * count)
        self.assertEqual(self.port.read_sizes, [MAX_READ_BYTES])
        self.assertGreater(self.port.in_waiting, 0)
        self.window._read_serial_data()
        self.assertEqual(self.window._sample_number, count)
        self.assertTrue(all(len(v) == 500 for v in self.window._sample_values.values()))
        self.assertLessEqual(self.window.data_log.blockCount(), 500)

    def test_bottom_anchor_and_scroll_after_resize_without_new_data(self):
        w = self.window
        w.show()
        self.receive(b'1 2 3 4 5 6\n' * 600)
        for size in [(720, 480), (1400, 900), (1000, 540)]:
            w.resize(*size)
            QTest.qWait(40)
            bar = w.data_log.verticalScrollBar()
            self.assertEqual(bar.value(), bar.maximum())
            bottom = w.data_log.mapTo(w.centralWidget(), QPoint(0, w.data_log.height())).y()
            self.assertLessEqual(w.centralWidget().height() - bottom, 12)
        w.showMaximized()
        QTest.qWait(50)
        self.assertEqual(w.data_log.verticalScrollBar().value(), w.data_log.verticalScrollBar().maximum())
        w.showNormal()
        QTest.qWait(50)
        self.assertEqual(w.data_log.verticalScrollBar().value(), w.data_log.verticalScrollBar().maximum())

    def test_reconnect_drops_partial_line_and_resets_all_curves(self):
        w = self.window
        self.receive(b'1 2 3 4 5 6\n7 8')
        w._render_plots()
        w._disconnect_port()
        self.assertEqual(len(w.channel_curves['X1'].getData()[0]), 1)
        new_port = BufferedPort()
        with patch('main_window.serial.Serial', return_value=new_port):
            w._toggle_connection()
        self.assertEqual(w._sample_number, 0)
        self.assertFalse(w._plot_buffer)
        self.assertTrue(all(not values for values in w._sample_values.values()))
        for curve in w.channel_curves.values():
            self.assertTrue(curve.getData()[0] is None or len(curve.getData()[0]) == 0)


if __name__ == '__main__':
    unittest.main()
