"""
/***************************************************************************
 *
 * ダイアログ表示モジュール（メインスレッド実行）
 *
 ***************************************************************************/
"""

from qgis.core import QgsMessageLog, Qgis
from PyQt5.QtCore import QObject, pyqtSlot, pyqtSignal, QMetaObject, Qt, QThread, QEventLoop, Q_ARG
from PyQt5.QtWidgets import QInputDialog, QApplication, QMessageBox


class DialogHelper(QObject):
    """メインスレッドでダイアログを表示するためのヘルパークラス"""

    dialog_finished = pyqtSignal(object, bool)  # (selected_item, ok)
    question_finished = pyqtSignal(bool)  # yes_clicked

    def __init__(self):
        super().__init__()
        self._result = None
        self._ok = False
        self._yes_clicked = False

    @pyqtSlot(list, str, str, list)
    def show_dialog_sync(self, sorted_items, title, label, item_displays):
        """メインスレッドでダイアログを表示

        Args:
            sorted_items: 選択肢のリスト（辞書のリスト）
            title: ダイアログのタイトル
            label: ダイアログのラベル
            item_displays: 表示用の文字列リスト
        """
        try:
            from qgis.utils import iface

            item, ok = QInputDialog.getItem(
                iface.mainWindow() if iface else None,
                title,
                label,
                item_displays,
                0,
                False
            )

            if ok and item:
                # 選択された項目を探す
                selected = next((x for x in sorted_items if x['display'] == item), None)
                self._result = selected
                self._ok = True
            else:
                self._result = None
                self._ok = False

            self.dialog_finished.emit(self._result, self._ok)

        except Exception as e:
            QgsMessageLog.logMessage(
                f"Dialog error: {str(e)}",
                "Plugin",
                Qgis.Warning,
            )
            self._result = None
            self._ok = False
            self.dialog_finished.emit(None, False)

    @pyqtSlot(str, str, str)
    def show_question_sync(self, title, text, informative_text):
        """メインスレッドでYes/Noダイアログを表示

        Args:
            title: ダイアログのタイトル
            text: メインテキスト
            informative_text: 補足テキスト
        """
        try:
            from qgis.utils import iface

            msg_box = QMessageBox(iface.mainWindow() if iface else None)
            msg_box.setIcon(QMessageBox.Question)
            msg_box.setWindowTitle(title)
            msg_box.setText(text)
            if informative_text:
                msg_box.setInformativeText(informative_text)
            msg_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            msg_box.setDefaultButton(QMessageBox.No)

            response = msg_box.exec_()
            self._yes_clicked = response == QMessageBox.Yes

            self.question_finished.emit(self._yes_clicked)

        except Exception as e:
            QgsMessageLog.logMessage(
                f"Question dialog error: {str(e)}",
                "Plugin",
                Qgis.Warning,
            )
            self._yes_clicked = False
            self.question_finished.emit(False)


class DialogManager:
    """ダイアログ表示"""

    _instance = None
    _helper = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if DialogManager._helper is None:
            # メインスレッドにDialogHelperを配置
            DialogManager._helper = DialogHelper()
            app = QApplication.instance()
            if app:
                DialogManager._helper.moveToThread(app.thread())

    def show_selection_dialog(self, sorted_items, title, label):
        """メインスレッドでダイアログを表示

        Args:
            sorted_items: 選択肢のリスト（辞書のリスト）
            title: ダイアログのタイトル
            label: ダイアログのラベル

        Returns:
            (selected_item, ok): 選択されたアイテムとOKボタンが押されたか
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
                return None, False

            main_thread = app.thread()

            # 表示用の文字列リスト
            item_displays = [item['display'] for item in sorted_items]

            if current_thread == main_thread:
                # 既にメインスレッドにいる場合は直接実行
                DialogManager._helper._result = None
                DialogManager._helper._ok = False
                DialogManager._helper.show_dialog_sync(sorted_items, title, label, item_displays)
                return DialogManager._helper._result, DialogManager._helper._ok
            else:
                # ワーカースレッドからメインスレッドを呼び出す
                loop = QEventLoop()
                result_container = [None, False]

                def on_finished(selected, ok):
                    result_container[0] = selected
                    result_container[1] = ok
                    loop.quit()

                # シグナルをコネクト
                DialogManager._helper.dialog_finished.connect(on_finished)

                # メインスレッドでメソッドを実行
                QMetaObject.invokeMethod(
                    DialogManager._helper,
                    "show_dialog_sync",
                    Qt.QueuedConnection,
                    Q_ARG(list, sorted_items),
                    Q_ARG(str, title),
                    Q_ARG(str, label),
                    Q_ARG(list, item_displays)
                )

                # 結果を待つ
                loop.exec_()

                # シグナルを切断
                DialogManager._helper.dialog_finished.disconnect(on_finished)

                return result_container[0], result_container[1]

        except Exception as e:
            QgsMessageLog.logMessage(
                f"Error showing dialog on main thread: {str(e)}",
                "Plugin",
                Qgis.Warning,
            )
            return None, False

    def show_question_dialog(self, title, text, informative_text=""):
        """メインスレッドでYes/Noダイアログを表示

        Args:
            title: ダイアログのタイトル
            text: メインテキスト
            informative_text: 補足テキスト（オプション）

        Returns:
            bool: Yesが押されたらTrue、Noが押されたらFalse
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
                return False

            main_thread = app.thread()

            if current_thread == main_thread:
                # 既にメインスレッドにいる場合は直接実行
                DialogManager._helper._yes_clicked = False
                DialogManager._helper.show_question_sync(title, text, informative_text)
                return DialogManager._helper._yes_clicked
            else:
                # ワーカースレッドからメインスレッドを呼び出す
                loop = QEventLoop()
                result_container = [False]

                def on_finished(yes_clicked):
                    result_container[0] = yes_clicked
                    loop.quit()

                # シグナルをコネクト
                DialogManager._helper.question_finished.connect(on_finished)

                # メインスレッドでメソッドを実行
                QMetaObject.invokeMethod(
                    DialogManager._helper,
                    "show_question_sync",
                    Qt.QueuedConnection,
                    Q_ARG(str, title),
                    Q_ARG(str, text),
                    Q_ARG(str, informative_text)
                )

                # 結果を待つ
                loop.exec_()

                # シグナルを切断
                DialogManager._helper.question_finished.disconnect(on_finished)

                return result_container[0]

        except Exception as e:
            QgsMessageLog.logMessage(
                f"Error showing question dialog on main thread: {str(e)}",
                "Plugin",
                Qgis.Warning,
            )
            return False
