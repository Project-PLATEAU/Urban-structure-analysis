"""
/***************************************************************************
 *
 * 空き家データ作成
 *
 ***************************************************************************/
"""

import os
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
import processing
from .gpkg_manager import GpkgManager


class VacancyDataGenerator:
    """空き家データ作成"""

    def __init__(self, base_path, check_canceled_callback=None, gpkg_manager=None):
        # GeoPackageマネージャーを初期化
        self.gpkg_manager = gpkg_manager
        # インプットデータパス
        self.base_path = base_path

        self.check_canceled = check_canceled_callback

    def tr(self, message):
        """翻訳用のメソッド"""
        return QCoreApplication.translate("VacancyDataGenerator", message)

    def create_vacancy(self):
        """空き家データ取り込み・作成処理"""
        try:
            # base_path 配下の「24_空き家ポイント」フォルダを再帰的に探索してShapefileを収集
            vacancy_folder = os.path.join(self.base_path, "24_空き家ポイント")
            shp_files = self.__get_shapefiles(vacancy_folder)

            # プロジェクトのCRSを取得
            project_crs = QgsProject.instance().crs()

            # 最初のシェープファイルからCRSを取得
            if shp_files:
                first_shp_file = shp_files[0]
                layer = QgsVectorLayer(
                    first_shp_file,
                    os.path.basename(first_shp_file),
                    "ogr"
                )
                if layer.isValid():
                    # ShapefileのCRSがプロジェクトのCRSと異なる場合、再投影
                    if layer.crs() != project_crs:
                        layer = processing.run(
                            "native:reprojectlayer",
                            {
                                'INPUT': layer,
                                'TARGET_CRS': project_crs,
                                'OUTPUT': 'memory:'
                            }
                        )['OUTPUT']
                    crs = layer.crs()
                else:
                    msg = self.tr(
                        "Failed to load layer: %1"
                    ).replace("%1", first_shp_file)
                    QgsMessageLog.logMessage(
                        msg,
                        self.tr("Plugin"),
                        Qgis.Warning,
                    )
                    return False
            else:
                data_name = self.tr("vacancy")
                msg = self.tr(
                    "The Shapefile for %1 was not found."
                ).replace("%1", data_name)
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Warning,
                )
                crs = project_crs

            # 一時メモリレイヤを作成
            vacancies_layer = QgsVectorLayer(
                f"Point?crs={crs.authid()}", "vacancies", "memory"
            )
            provider = vacancies_layer.dataProvider()
            provider.addAttributes([QgsField("year", QVariant.String)])
            vacancies_layer.updateFields()

            # 各シェープファイルを処理
            for shp_file in shp_files:
                if self.check_canceled():
                    return False  # キャンセルチェック

                # フォルダ名から年度を取得
                folder = os.path.basename(os.path.dirname(shp_file))
                year_str = folder.replace('年', '')
                year = int(year_str) if year_str.isdigit() else None

                encoding = self.__detect_encoding(shp_file)
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

                # ShapefileのCRSがプロジェクトのCRSと異なる場合、再投影
                if layer.crs() != project_crs:
                    layer = processing.run(
                        "native:reprojectlayer",
                        {
                            'INPUT': layer,
                            'TARGET_CRS': project_crs,
                            'OUTPUT': 'memory:'
                        }
                    )['OUTPUT']

                # フィーチャの追加
                for feature in layer.getFeatures():
                    if self.check_canceled():
                        return False  # キャンセルチェック

                    new_feature = QgsFeature()
                    new_feature.setGeometry(feature.geometry())
                    new_feature.setAttributes([year])  # year フィールドのみ設定
                    provider.addFeature(new_feature)

            # vacanciesレイヤをGeoPackageに保存
            if not self.gpkg_manager.add_layer(
                vacancies_layer, "vacancies", "空き家"
            ):
                raise Exception(self.tr("Failed to add layer to GeoPackage."))

            data_name = self.tr("vacancy")
            msg = self.tr(
                "%1 data generation completed."
            ).replace("%1", data_name)
            QgsMessageLog.logMessage(
                msg,
                self.tr("Plugin"),
                Qgis.Info,
            )
            return True

        except Exception as error:
            QgsMessageLog.logMessage(
                self.tr("An error occurred: %1").replace("%1", error),
                self.tr("Plugin"),
                Qgis.Critical,
            )
            raise error

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
            with open(dbf_file, 'rb') as file:
                raw_data = file.read()
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
