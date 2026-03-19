"""
/***************************************************************************
 *
 * ゾーンポリゴン作成
 *
 ***************************************************************************/
"""

import os
import processing
import chardet
from qgis.core import (
    QgsMessageLog,
    Qgis,
    QgsVectorLayer,
    QgsField,
    QgsFeature,
    QgsProject,
)
from PyQt5.QtCore import QCoreApplication, QVariant
from .gpkg_manager import GpkgManager
from .dialog_helper import DialogManager


class ZoneDataGenerator:
    """ゾーンポリゴンデータ取り込み・レイヤ作成"""
    def __init__(self, base_path, check_canceled_callback=None, cancel_callback=None, gpkg_manager=None):
        # GeoPackageマネージャーを初期化
        self.gpkg_manager = gpkg_manager
        # インプットデータパス
        self.base_path = base_path

        self.check_canceled = check_canceled_callback
        self.cancel_callback = cancel_callback  # キャンセルフラグを設定するコールバック

        # ダイアログマネージャー（メインスレッドで実行）
        self.dialog_manager = DialogManager()

    def tr(self, message):
        """翻訳用のメソッド"""
        return QCoreApplication.translate(self.__class__.__name__, message)

    def create_zone(self):
        """ゾーンポリゴン 作成"""
        try:
            # base_path 配下の「02_ゾーンポリゴン」フォルダを再帰的に探索してShapefileを収集
            induction_area_folder = os.path.join(self.base_path, "02_ゾーンポリゴン")
            shp_files = self.__get_shapefiles(induction_area_folder)

            # プロジェクトのCRSを取得
            project_crs = QgsProject.instance().crs()

            # レイヤを格納するリスト
            layers = []

            for shp_file in shp_files:
                if self.check_canceled():
                    return False  # キャンセルチェック

                # エンコーディングを自動検出
                encoding = self.__detect_encoding(shp_file)

                # Shapefile読み込み
                layer = QgsVectorLayer(
                    shp_file,
                    os.path.basename(shp_file),
                    "ogr"
                )
                layer.setProviderEncoding(encoding)

                if not layer.isValid():
                    msg = self.tr(
                        "Failed to load layer: %1"
                    ).replace("%1", shp_file)
                    QgsMessageLog.logMessage(
                        msg,
                        self.tr("Plugin"),
                        Qgis.Warning,
                    )
                    continue

                # プロジェクトのCRSに再投影
                if layer.crs() != project_crs:
                    layer = processing.run("native:reprojectlayer", {
                        'INPUT': layer,
                        'TARGET_CRS': project_crs,
                        'OUTPUT': 'memory:'
                    })['OUTPUT']

                # フィールド名をリネーム
                provider = layer.dataProvider()
                field_mapping = {
                    "N03_001": "prefecture_name",  # 都道府県名
                    "N03_002": "subprefecture_name",  # 北海道の振興局名
                    "N03_003": "county_name",  # 郡名
                    "N03_004": "city_name",  # 市区町村名
                    "N03_005": "district_name",  # 政令指定都市の行政区名
                    "N03_007": "code",  # 全国地方公共団体コード
                }

                # 既存フィールドをリネーム
                for old_name, new_name in field_mapping.items():
                    if old_name in layer.fields().names():
                        idx = layer.fields().indexOf(old_name)
                        provider.renameAttributes({idx: new_name})

                layer.updateFields()
                layers.append(layer)

            if not layers:
                data_name = self.tr("zone")
                msg = (
                    self.tr("No valid %1 Shapefile was found.")
                    .replace("%1", data_name)
                )
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Info,
                )
                return False

            # 複数のレイヤをマージ
            merged_layer = self.__merge_layers(layers)

            # 無効なジオメトリを修復
            merged_layer = self.__fix_invalid_geometries(merged_layer)

            # nameフィールドを追加して値を設定
            merged_layer = self.__add_name_field(merged_layer)

            # is_targetフィールドを追加（デフォルト値0）
            provider = merged_layer.dataProvider()
            provider.addAttributes([QgsField("is_target", QVariant.Int)])
            merged_layer.updateFields()

            # 全フィーチャをis_target=0（非集計対象）として設定
            field_idx = merged_layer.fields().indexOf("is_target")
            updates = {}
            for feature in merged_layer.getFeatures():
                updates[feature.id()] = {field_idx: 0}
            provider.changeAttributeValues(updates)

            # 市区町村の選択ダイアログを表示
            result = self.__select_target_municipality(merged_layer)
            if result is False:
                # キャンセルされた場合、キャンセルフラグを設定
                if self.cancel_callback:
                    self.cancel_callback()
                return False
            merged_layer = result

            # 空間インデックス作成
            processing.run("native:createspatialindex", {'INPUT': merged_layer})

            # zonesレイヤをGeoPackageに保存
            if not self.gpkg_manager.add_layer(merged_layer, "zones", "行政区域"):
                raise Exception(self.tr("Failed to add layer to GeoPackage."))

            data_name = self.tr("zone")
            msg = self.tr(
                "%1 data generation completed."
            ).replace("%1", data_name)
            QgsMessageLog.logMessage(
                msg,
                self.tr("Plugin"),
                Qgis.Info,
            )
            return True

        except Exception as e:
            QgsMessageLog.logMessage(
                self.tr("An error occurred: %1").replace("%1", str(e)),
                self.tr("Plugin"),
                Qgis.Critical,
            )
            raise e

    def __merge_layers(self, layers):
        """複数のレイヤを1つにマージ"""
        result = processing.run(
            "native:mergevectorlayers",
            {
                'LAYERS': layers,
                'CRS': layers[0].crs().authid(),
                'OUTPUT': 'memory:merged_layer',
            },
        )

        return result['OUTPUT']

    def __get_shapefiles(self, directory):
        """指定されたディレクトリ配下のすべてのShapefile (.shp) を再帰的に取得する"""
        msg = self.tr("Directory: %1").replace("%1", directory)
        QgsMessageLog.logMessage(
            msg,
            self.tr("Plugin"),
            Qgis.Info,
        )

        shp_files = []
        for root, _, files in os.walk(directory):
            for file in files:
                if file.endswith(".shp"):
                    shp_files.append(os.path.join(root, file))
        return shp_files

    def __select_target_municipality(self, layer):
        """市区町村を選択してis_targetフラグを設定"""
        # 市区町村リストを作成（重複を除去）
        municipalities = {}
        for feature in layer.getFeatures():
            name = feature['name']
            code = feature['code']
            if name and code:
                # キーはnameで重複を防ぐ（同じ市の複数の区をまとめる）
                if name not in municipalities:
                    municipalities[name] = {
                        'name': name,
                        'codes': set()
                    }
                municipalities[name]['codes'].add(code)

        if not municipalities:
            msg = self.tr("No valid municipalities found in the zones layer.")
            QgsMessageLog.logMessage(
                msg,
                self.tr("Plugin"),
                Qgis.Warning,
            )
            return layer

        # 表示用にコードをソートして文字列化
        for name, data in municipalities.items():
            sorted_codes = sorted(data['codes'])
            data['code'] = sorted_codes[0]  # ソート用に最小のコードを保存
            data['display'] = f"{name}（{', '.join(sorted_codes)}）"

        # ソートされたリストを作成
        sorted_items = sorted(municipalities.values(), key=lambda x: x['code'])

        # メインスレッドでダイアログを表示
        selected_municipality, ok = self.dialog_manager.show_selection_dialog(
            sorted_items,
            self.tr("Select Target Municipality"),
            self.tr("Please select the target municipality for aggregation:")
        )

        # キャンセルされた場合はFalseを返す
        if not ok or selected_municipality is None:
            return False

        # is_targetフィールドのインデックスを取得
        field_idx = layer.fields().indexOf("is_target")

        # 選択された市区町村のフィーチャにis_target=1を設定
        # nameが一致するすべてのフィーチャを選択（複数の区をまとめて選択）
        provider = layer.dataProvider()
        updates = {}

        for feature in layer.getFeatures():
            if feature['name'] == selected_municipality['name']:
                updates[feature.id()] = {field_idx: 1}

        if updates:
            provider.changeAttributeValues(updates)

            msg = self.tr("Selected target municipality: %1").replace(
                "%1", selected_municipality['display']
            )
            QgsMessageLog.logMessage(
                msg,
                self.tr("Plugin"),
                Qgis.Info,
            )
        else:
            msg = self.tr("Failed to set target municipality.")
            QgsMessageLog.logMessage(
                msg,
                self.tr("Plugin"),
                Qgis.Warning,
            )

        return layer

    def __fix_invalid_geometries(self, layer):
        """Fix invalid geometries in the layer"""
        msg_start = self.tr(
            "Fixing invalid geometries in layer: %1."
        ).replace("%1", layer.name())
        QgsMessageLog.logMessage(
            msg_start,
            self.tr("Plugin"),
            Qgis.Info,
        )
        result = processing.run(
            "native:fixgeometries",
            {'INPUT': layer, 'OUTPUT': 'memory:fixed_layer'},
        )
        msg_complete = self.tr(
            "Completed fixing invalid geometries in layer: %1."
        ).replace("%1", layer.name())
        QgsMessageLog.logMessage(
            msg_complete,
            self.tr("Plugin"),
            Qgis.Info,
        )
        return result['OUTPUT']

    def __add_name_field(self, layer):
        """nameフィールドを追加して、county_nameまたはcity_nameを設定"""
        # nameフィールドを追加
        provider = layer.dataProvider()
        provider.addAttributes([QgsField("name", QVariant.String)])
        layer.updateFields()

        # 各フィーチャのname値を設定
        name_idx = layer.fields().indexOf("name")

        updates = {}
        for feature in layer.getFeatures():
            county_name = feature['county_name']
            city_name = feature['city_name']

            # county_nameに「市」が含まれているかチェック
            if county_name and '市' in county_name:
                name_value = county_name
            else:
                # county_nameに「市」がない、または空、NULLの場合はcity_nameを使用
                name_value = city_name if city_name else ''

            updates[feature.id()] = {name_idx: name_value}

        if updates:
            provider.changeAttributeValues(updates)

        msg = self.tr("Added 'name' field to zones layer.")
        QgsMessageLog.logMessage(
            msg,
            self.tr("Plugin"),
            Qgis.Info,
        )

        return layer

    def __detect_encoding(self, file_path):
        """Shapefile に対応する DBF ファイルのエンコーディングを検出"""
        dbf_file = file_path.replace(
            '.shp', '.dbf'
        )  # shpに対応する .dbf ファイルのパス
        if os.path.exists(dbf_file):
            with open(dbf_file, 'rb') as f:
                raw_data = f.read()
                result = chardet.detect(raw_data)
                encoding = result.get('encoding')

                # encodingがNoneの場合はデフォルトをUTF-8に
                if not encoding:
                    encoding = 'UTF-8'

                if encoding == 'MacRoman':
                    msg = self.tr(
                        "%1 was detected. Using SHIFT_JIS for the file %2."
                    ).replace("%1", "MacRoman").replace("%2", dbf_file)
                    QgsMessageLog.logMessage(
                        msg,
                        self.tr("Plugin"),
                        Qgis.Info,
                    )
                    encoding = 'SHIFT_JIS'

                if encoding == 'Windows-1254':
                    msg = self.tr(
                        "%1 was detected. Using SHIFT_JIS for the file %2."
                    ).replace("%1", "Windows-1254").replace("%2", dbf_file)
                    QgsMessageLog.logMessage(
                        msg,
                        self.tr("Plugin"),
                        Qgis.Info,
                    )
                    encoding = 'SHIFT_JIS'

                if encoding == 'Windows-1252':
                    msg = self.tr(
                        "%1 was detected. Using SHIFT_JIS for the file %2."
                    ).replace("%1", "Windows-1252").replace("%2", dbf_file)
                    QgsMessageLog.logMessage(
                        msg,
                        self.tr("Plugin"),
                        Qgis.Info,
                    )
                    encoding = 'SHIFT_JIS'

                msg = self.tr("Encoding: %1").replace("%1", encoding)
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Info,
                )
                return encoding if encoding else 'SHIFT_JIS'
        else:
            msg = self.tr(
                "No corresponding DBF file was found for the specified path: "
                "%1."
            ).replace("%1", file_path)
            QgsMessageLog.logMessage(
                msg,
                self.tr("Plugin"),
                Qgis.Warning,
            )
            return 'UTF-8'
