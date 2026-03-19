"""
/***************************************************************************
 *
 * Excel読み込みモジュール
 *
 ***************************************************************************/
"""

import pandas as pd
from qgis.core import QgsMessageLog, Qgis
from PyQt5.QtCore import QObject, pyqtSlot, pyqtSignal, QMetaObject, Qt, QThread, QEventLoop
from PyQt5.QtWidgets import QApplication


class ExcelReaderHelper(QObject):
    """メインスレッドでExcelを読み込むためのヘルパークラス"""

    # シグナル：読み込み完了を通知
    read_finished = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self._result = None

    @pyqtSlot(str, str)
    def read_excel_sync(self, filepath, engine):
        """メインスレッドでExcelを読み込む

        Args:
            filepath: Excelファイルのパス
            engine: pandasのengine ('openpyxl', 'xlrd', or '')
        """
        try:
            if engine and engine != '':
                data = pd.read_excel(filepath, sheet_name=None, engine=engine)
            else:
                data = pd.read_excel(filepath, sheet_name=None)

            # ガベージコレクション実行
            import gc
            gc.collect()

            self._result = data
            self.read_finished.emit(data)
        except Exception as e:
            QgsMessageLog.logMessage(
                f"Excel reading error: {str(e)}",
                "Plugin",
                Qgis.Warning,
            )
            self._result = None
            self.read_finished.emit(None)


class ExcelReader:
    """Excel読み込み"""

    _instance = None
    _helper = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if ExcelReader._helper is None:
            # メインスレッドにExcelReaderHelperを配置
            ExcelReader._helper = ExcelReaderHelper()
            app = QApplication.instance()
            if app:
                ExcelReader._helper.moveToThread(app.thread())

    def read_excel(self, filepath, engine=None):
        """メインスレッドでExcelファイルを読み込む

        Args:
            filepath: Excelファイルのパス
            engine: pandasのengine ('openpyxl', 'xlrd', or None)

        Returns:
            読み込んだデータ、またはNone
        """
        try:
            # 現在のスレッドがメインスレッドかチェック
            current_thread = QThread.currentThread()
            app = QApplication.instance()
            if not app:
                QgsMessageLog.logMessage(
                    "QApplication not available",
                    "Plugin",
                    Qgis.Warning,
                )
                return None

            main_thread = app.thread()

            if current_thread == main_thread:
                # 既にメインスレッドにいる場合は直接実行
                ExcelReader._helper._result = None
                ExcelReader._helper.read_excel_sync(filepath, engine or '')
                return ExcelReader._helper._result
            else:
                # ワーカースレッドからメインスレッドを呼び出す
                # イベントループで結果を待つ
                loop = QEventLoop()
                result_container = [None]

                def on_finished(data):
                    result_container[0] = data
                    loop.quit()

                # シグナルをコネクト
                ExcelReader._helper.read_finished.connect(on_finished)

                # メインスレッドでメソッドを実行
                from PyQt5.QtCore import Q_ARG
                QMetaObject.invokeMethod(
                    ExcelReader._helper,
                    "read_excel_sync",
                    Qt.QueuedConnection,
                    Q_ARG(str, filepath),
                    Q_ARG(str, engine or '')
                )

                # 結果を待つ
                loop.exec_()

                # シグナルを切断
                ExcelReader._helper.read_finished.disconnect(on_finished)

                return result_container[0]

        except Exception as e:
            QgsMessageLog.logMessage(
                f"Error invoking Excel read on main thread: {str(e)}",
                "Plugin",
                Qgis.Warning,
            )
            return None
