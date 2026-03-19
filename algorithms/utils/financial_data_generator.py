"""
/***************************************************************************
 *
 * 【FN008】財政関連データ作成機能
 *
 ***************************************************************************/
"""

import os
import re
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


class FinancialDataGenerator:
    """財政関連データ作成機能"""
    def __init__(self, base_path, check_canceled_callback=None, gpkg_manager=None):
        # GeoPackageマネージャーを初期化
        self.gpkg_manager = gpkg_manager
        # インプットデータパス
        self.base_path = base_path

        self.check_canceled = check_canceled_callback

    def tr(self, message):
        """翻訳用のメソッド"""
        return QCoreApplication.translate(self.__class__.__name__, message)

    def create_land_price(self):
        """地価公示作成"""
        try:
            # base_path 配下の「23_地価公示」フォルダを再帰的に探索してShapefileを収集
            induction_area_folder = os.path.join(self.base_path, "23_地価公示")
            shp_files = self.__get_shapefiles(induction_area_folder)

            # レイヤを格納するリスト
            layers = []

            for shp_file in shp_files:
                if self.check_canceled():
                    return  # キャンセルチェック
                encoding = self.__detect_encoding(shp_file)

                # Shapefile 読み込み
                layer = QgsVectorLayer(
                    shp_file, os.path.basename(shp_file), "ogr"
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

                # 年度を判定（L01_007またはL01_006から取得）
                year_field = None

                # 年度フィールドを特定する
                if "L01_007" in layer.fields().names() and re.match(
                    r'^\d{4}$', str(layer.getFeature(0)["L01_007"])
                ):  # 2024年以降
                    year_field = "L01_007"
                elif "L01_005" in layer.fields().names() and re.match(
                    r'^\d{4}$', str(layer.getFeature(0)["L01_005"])
                ):  # 2023年以前
                    year_field = "L01_005"
                else:
                    msg = self.tr(
                        "The year field was not found in %1."
                    ).replace("%1", shp_file)

                    QgsMessageLog.logMessage(
                        msg,
                        self.tr("Plugin"),
                        Qgis.Warning,
                    )
                    continue

                # 一時メモリレイヤを作成し、Shapefileのデータを取り込み
                temp_layer = QgsVectorLayer(
                    f"Point?crs={layer.crs().authid()}", "land_prices", "memory"
                )
                temp_provider = temp_layer.dataProvider()

                # 必要なフィールドを追加
                temp_provider.addAttributes(
                    [
                        QgsField("administrative_area_code", QVariant.String),
                        QgsField("usage_classification", QVariant.String),
                        QgsField("serial_number", QVariant.String),
                        QgsField(
                            "previous_year_administrative_area_code",
                            QVariant.String,
                        ),
                        QgsField(
                            "previous_year_usage_category", QVariant.String
                        ),
                        QgsField(
                            "previous_year_serial_number", QVariant.String
                        ),
                        QgsField("year", QVariant.String),
                        QgsField("public_land_price", QVariant.Int),
                        QgsField("year_change_rate", QVariant.Double),
                    ]
                )
                temp_layer.updateFields()

                # フィーチャの追加
                for feature in layer.getFeatures():
                    if self.check_canceled():
                        return  # キャンセルチェック
                    new_feature = QgsFeature()
                    new_feature.setGeometry(feature.geometry())

                    if year_field == "L01_007":  # 2024年以降のデータ
                        attributes = [
                            feature["L01_001"],  # administrative_area_code
                            feature["L01_002"],  # usage_classification
                            feature["L01_003"],  # serial_number
                            feature[
                                "L01_004"
                            ],  # previous_year_administrative_area_code
                            feature["L01_005"],  # previous_year_usage_category
                            feature["L01_006"],  # previous_year_serial_number
                            feature["L01_007"],  # year
                            feature["L01_008"],  # public_land_price
                            feature["L01_009"],  # year_change_rate
                        ]
                    else:  # 2023年以前のデータ（yearとpublic_land_priceのみ）
                        attributes = [
                            None,  # administrative_area_code
                            None,  # usage_classification
                            None,  # serial_number
                            None,  # previous_year_administrative_area_code
                            None,  # previous_year_usage_category
                            None,  # previous_year_serial_number
                            feature["L01_005"],  # year
                            feature["L01_006"],  # public_land_price
                            None,  # year_change_rate
                        ]

                    new_feature.setAttributes(attributes)
                    temp_provider.addFeature(new_feature)

                layers.append(temp_layer)

            if not layers:
                data_name = self.tr("land price")
                msg = (
                    self.tr("No valid %1 Shapefile was found.")
                    .replace("%1", data_name)
                )
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Info,
                )
                raise Exception(msg)

            # 複数のレイヤをマージ
            merged_layer = self.__merge_layers(layers)

            # 空間インデックス作成
            processing.run("native:createspatialindex",
                           {'INPUT': merged_layer})

            # メッシュレイヤ取得
            meshes_layer = self.gpkg_manager.load_layer(
                'meshes', withload_project=False
            )
            if not meshes_layer:
                raise Exception(self.tr("The %1 layer was not found.")
                    .replace("%1", "meshes"))

            # メッシュレイヤに地価公示平均、増減を追加
            self.calculate_average_and_diff(meshes_layer, merged_layer)

            # land_pricesレイヤをGeoPackageに保存
            if not self.gpkg_manager.add_layer(
                merged_layer, "land_prices", "地価公示"
            ):
                raise Exception(self.tr("Failed to add layer to GeoPackage."))

            self.gpkg_manager.load_layer('meshes', '公示地価メッシュ')

            data_name = self.tr("land price")
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

    def calculate_average_and_diff(self, meshes_layer, merged_layer):
        """各メッシュ内の年度ごとの平均地価と増減を計算してメッシュレイヤに追加"""
        try:
            # 地価公示の年度を取得
            unique_years = sorted(
                set(feature["year"] for feature in merged_layer.getFeatures())
            )
            if not unique_years:
                layer_name = self.tr("land price layer")
                msg = self.tr(
                    "The %1 layer does not contain year data."
                ).replace("%1", layer_name)
                raise Exception(msg)

            meshes_provider = meshes_layer.dataProvider()

            # メッシュレイヤに年度ごとの平均地価フィールドを追加
            fields_to_add = []
            for year in unique_years:
                avg_field_name = f"average_land_price_{year}"
                if meshes_layer.fields().indexOf(avg_field_name) == -1:
                    fields_to_add.append(
                        QgsField(avg_field_name, QVariant.Double)
                    )

            # メッシュレイヤに年度ごとの増減フィールドを追加
            for i in range(1, len(unique_years)):
                diff_field_name = f"diff_land_price_{unique_years[i]}"
                if meshes_layer.fields().indexOf(diff_field_name) == -1:
                    fields_to_add.append(
                        QgsField(diff_field_name, QVariant.Double)
                    )

            # 属性を追加
            if fields_to_add:
                meshes_provider.addAttributes(fields_to_add)
                meshes_layer.updateFields()

            # 編集モードを開始
            if not meshes_layer.startEditing():
                raise Exception(
                    "メッシュレイヤの編集モードを開始できませんでした。"
                )

            # メッシュごとに平均地価と増減を計算
            for mesh_feature in meshes_layer.getFeatures():
                mesh_geometry = mesh_feature.geometry()

                # メッシュの範囲に含まれる地価公示ポイントを抽出
                filtered_features = [
                    feature
                    for feature in merged_layer.getFeatures()
                    if feature.geometry().within(mesh_geometry)
                ]

                # 年度ごとの平均地価を計算
                year_to_avg = {}
                for year in unique_years:
                    points_in_year = [
                        f for f in filtered_features if f["year"] == year
                    ]
                    if points_in_year:
                        total_price = sum(
                            f["public_land_price"] for f in points_in_year
                        )
                        avg_price = total_price / len(points_in_year)
                    else:
                        avg_price = None

                    year_to_avg[year] = avg_price

                # メッシュに年度ごとの平均地価を設定
                for year, avg in year_to_avg.items():
                    avg_field_name = f"average_land_price_{year}"
                    mesh_feature[avg_field_name] = avg

                # 年度ごとの増減を計算
                for i in range(1, len(unique_years)):
                    prev_year = unique_years[i - 1]
                    curr_year = unique_years[i]
                    diff_field_name = f"diff_land_price_{curr_year}"
                    avg_prev = year_to_avg.get(prev_year)
                    avg_curr = year_to_avg.get(curr_year)

                    if avg_prev is not None and avg_curr is not None:
                        diff = avg_curr - avg_prev
                    else:
                        diff = None

                    mesh_feature[diff_field_name] = diff

                # メッシュフィーチャを更新
                if not meshes_layer.updateFeature(mesh_feature):
                    raise Exception(
                        f"メッシュフィーチャ {mesh_feature.id()} の更新に失敗しました。"
                    )

            # 変更をコミット
            meshes_layer.commitChanges()

            msg = self.tr(
                "Added average land price and its changes to the mesh layer."
            )

            QgsMessageLog.logMessage(
                msg,
                self.tr("Plugin"),
                Qgis.Info,
            )

        except Exception as e:
            # 編集モードを終了せずに例外を投げる
            if meshes_layer.isEditable():
                meshes_layer.rollBack()

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

    def __detect_encoding(self, file_path):
        """Shapefile に対応する DBF ファイルのエンコーディングを検出"""
        dbf_file = file_path.replace(
            '.shp', '.dbf'
        )  # shpに対応する .dbf ファイルのパス
        if os.path.exists(dbf_file):
            with open(dbf_file, 'rb') as f:
                raw_data = f.read()
                result = chardet.detect(raw_data)
                encoding = result['encoding']
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

                msg = self.tr("Detected encoding: %1").replace("%1", encoding)
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
