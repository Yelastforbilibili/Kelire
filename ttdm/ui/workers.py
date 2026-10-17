"""把阻塞的网络调用丢到线程池，结果通过信号回到界面线程。"""

from __future__ import annotations

from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class TaskSignals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], Any], signals: TaskSignals) -> None:
        super().__init__()
        self.fn = fn
        self.signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            result = self.fn()
        except Exception as exc:  # noqa: BLE001 - 统一转成界面能展示的文本
            message = str(exc).strip() or exc.__class__.__name__
            try:
                self.signals.failed.emit(message)
            except RuntimeError:
                pass
            return
        try:
            self.signals.done.emit(result)
        except RuntimeError:
            pass


class TaskRunner(QObject):
    """界面侧的统一入口：run(fn, on_done, on_error)，持有信号对象防止回收。"""

    def __init__(self, parent: QObject, threads: int = 6) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max(2, threads))
        self._live: list[TaskSignals] = []
        self._closed = False

    def run(
        self,
        fn: Callable[[], Any],
        on_done: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        if self._closed:
            return
        signals = TaskSignals(self)
        if on_done is not None:
            signals.done.connect(on_done)
        signals.failed.connect(on_error or (lambda message: None))
        signals.done.connect(lambda *_: self._release(signals))
        signals.failed.connect(lambda *_: self._release(signals))
        self._live.append(signals)
        self._pool.start(_Task(fn, signals))

    def _release(self, signals: TaskSignals) -> None:
        try:
            self._live.remove(signals)
        except ValueError:
            pass
        signals.deleteLater()

    def wait(self, msec: int = 2000) -> None:
        self._pool.waitForDone(msec)

    def close(self) -> None:
        self._closed = True
        self._pool.clear()
        self._pool.waitForDone(1500)
