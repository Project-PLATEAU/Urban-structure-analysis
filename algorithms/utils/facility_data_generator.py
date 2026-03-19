"""
/***************************************************************************
 *
 * 【FN004】施設関連データ作成機能
 *
 ***************************************************************************/
"""

import os
import re
import chardet
import json
import processing
from qgis.core import (
    QgsMessageLog,
    Qgis,
    QgsVectorLayer,
    QgsField,
    QgsFeature,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCoordinateTransformContext,
)
from PyQt5.QtCore import QCoreApplication, QVariant
from .gpkg_manager import GpkgManager

class FacilityDataGenerator:
    """施設関連データ作成機能"""
    FACILITY_TYPES = {
        "8_都市機能誘導施設": 0,
        "1_行政機能": 1,
        "7_商業機能": 2,
        "4_医療機能": 3,
        "6_子育て機能": 4,
        "3_介護・福祉機能": 5,
        "5_教育機能": 6,
        "2_文化交流機能": 7,
    }


    def __init__(self, base_path, check_canceled_callback=None, gpkg_manager=None):
        # GeoPackageマネージャーを初期化
        self.gpkg_manager = gpkg_manager
        # インプットデータパス
        self.base_path = base_path

        self.check_canceled = check_canceled_callback

    def tr(self, message):
        """翻訳用のメソッド"""
        return QCoreApplication.translate(self.__class__.__name__, message)

    def load_facilities(self):
        """施設データ取り込み"""
        try:
            layers = []
            target_years = ["設定年", "最新年"]

            for facility_type, file_type in self.FACILITY_TYPES.items():
                for year_folder in target_years:
                    facility_folder = os.path.join(
                        self.base_path, "08_施設", facility_type, year_folder
                    )

                    if not os.path.exists(facility_folder):
                        continue

                    shp_files = self.__get_shapefiles(facility_folder)

                    if not shp_files:
                        msg = self.tr(
                            "The Shapefile for %1/%2 was not found."
                        ).replace("%1", facility_type).replace("%2", year_folder)

                        QgsMessageLog.logMessage(
                            msg,
                            self.tr("Plugin"),
                            Qgis.Warning,
                        )
                        continue

                    if self.check_canceled():
                        return  # キャンセルチェック

                    for shp_file in shp_files:
                        year = year_folder  # "設定年" or "最新年"
                        encoding = self.__detect_encoding(shp_file)

                        # Shapefile 読み込み時にエンコーディングを指定
                        layer = QgsVectorLayer(
                            shp_file, os.path.basename(shp_file), "ogr"
                        )
                        layer.setProviderEncoding(encoding)

                        # レイヤの有効性を確認
                        if not layer.isValid():
                            msg = self.tr(
                                "Failed to load layer: %1"
                            ).replace("%1", shp_file)
                            QgsMessageLog.logMessage(
                                msg,
                                self.tr("Plugin"),
                                Qgis.Warning,
                            )

                        layers.append((layer, year, file_type))

                        if self.check_canceled():
                            return  # キャンセルチェック

            # レイヤを結合し、施設データを作成
            facility_layer = self.__create_facilities_layer(layers)

            if not self.gpkg_manager.add_layer(
                facility_layer, "facilities", "都市施設"
            ):
                raise Exception(self.tr("Failed to add layer to GeoPackage."))

            data_name = self.tr("facility")
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

    def __create_facilities_layer(self, layers):
        """複数の施設レイヤを1つの統合レイヤに変換"""
        # 統合レイヤを作成
        fields = [
            QgsField("year", QVariant.String),
            QgsField("name", QVariant.String),
            QgsField("type", QVariant.Int),
            QgsField("address", QVariant.String),
            QgsField("properties", QVariant.String),
        ]
        facility_layer = QgsVectorLayer(
            "Point?crs=EPSG:4326", "facilities", "memory"
        )
        provider = facility_layer.dataProvider()
        provider.addAttributes(fields)
        facility_layer.updateFields()

        # レイヤの編集を開始
        facility_layer.startEditing()

        # 都市施設CRS（EPSG:4326）
        target_crs = QgsCoordinateReferenceSystem("EPSG:4326")

        # 施設データの収集と統合
        for layer, year, file_type in layers:
            if self.check_canceled():
                return  # キャンセルチェック

            # 元のレイヤのCRSを確認し、必要ならレイヤ全体を変換
            source_crs = layer.crs()
            if source_crs != target_crs:
                msg = self.tr(
                    "Transforming CRS from %1 to %2 for layer: %3"
                ).replace("%1", source_crs.authid()
                ).replace("%2", target_crs.authid()
                ).replace("%3", layer.name())
                QgsMessageLog.logMessage(msg, self.tr("Plugin"), Qgis.Info)

                # レイヤ全体をCRS変換
                reprojected_result = processing.run(
                    "native:reprojectlayer",
                    {
                        'INPUT': layer,
                        'TARGET_CRS': target_crs,
                        'OUTPUT': 'memory:'
                    }
                )
                layer = reprojected_result['OUTPUT']

            for feature in layer.getFeatures():
                new_feature = QgsFeature()
                new_feature.setGeometry(feature.geometry())
                new_feature.setFields(facility_layer.fields())

                # 介護・福祉機能はP14_005属性で子育て(4)/福祉(5)を判別、なければ全件福祉(5)
                # 子育て機能は全件子育て(4)として取り込む
                if file_type == 5:
                    # 介護・福祉機能フォルダの場合
                    if "P14_005" in feature.fields().names() and feature["P14_005"] is not None:
                        type_code = self.__get_welfare_type(feature["P14_005"])
                    else:
                        type_code = 5  # P14_005がなければ福祉機能
                else:
                    type_code = file_type

                # すべての属性をJSON形式で保存
                attributes = {}
                for field_name in feature.fields().names():
                    value = feature[field_name]
                    # QVariantをPython型に変換
                    if value is not None and not isinstance(value, (str, int, float, bool)):
                        value = str(value)
                    attributes[field_name] = value

                properties_json = ""
                if attributes:
                    properties_json = json.dumps(attributes, ensure_ascii=False)

                # フィーチャの属性を設定
                new_feature.setAttribute("year", year)
                new_feature.setAttribute("name", "")
                new_feature.setAttribute("type", type_code)
                new_feature.setAttribute("address", "")
                new_feature.setAttribute("properties", properties_json)
                provider.addFeature(new_feature)

        # 編集内容をコミットして保存
        facility_layer.commitChanges()
        facility_layer.updateExtents()

        return facility_layer

    def __get_welfare_type(self, data):
        """福祉施設ポイントのP14_005属性に基づいて施設タイプを判別"""
        if data in ('05', '06'):
            return 4  # 子育て施設
        return 5  # 福祉施設（'01', '02', '03', '04', '99'..etc)

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
                return encoding if encoding else 'UTF-8'
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
