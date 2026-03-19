"""
/***************************************************************************
 *
 * 【FN005】交通関連データ作成機能
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
)
from PyQt5.QtCore import QCoreApplication, QVariant

from .gpkg_manager import GpkgManager

class TransportationDataGenerator:
    """交通関連データ作成機能"""
    def __init__(self, base_path, check_canceled_callback=None, gpkg_manager=None):
        # GeoPackageマネージャーを初期化
        self.gpkg_manager = gpkg_manager
        # インプットデータパス
        self.base_path = base_path

        self.check_canceled = check_canceled_callback

    def tr(self, message):
        """翻訳用のメソッド"""
        return QCoreApplication.translate(self.__class__.__name__, message)

    def load_transportations(self):
        """交通関連データ作成処理"""
        try:
            if self.check_canceled():
                return  # キャンセルチェック
            self.create_road_networks()
            if self.check_canceled():
                return  # キャンセルチェック
            self.create_railway_stations()
            if self.check_canceled():
                return  # キャンセルチェック
            self.create_railway_networks()
            if self.check_canceled():
                return  # キャンセルチェック
            self.create_bus_stops()
            if self.check_canceled():
                return  # キャンセルチェック
            self.create_bus_networks()

            return True
        except Exception as e:
            QgsMessageLog.logMessage(
                self.tr("An error occurred: %1").replace("%1", str(e)),
                self.tr("Plugin"),
                Qgis.Critical,
            )
            raise e

    def create_road_networks(self):
        """道路ネットワーク作成"""
        try:
            # base_path 配下の「07_道路ネットワーク」フォルダを再帰的に探索してShapefileを収集
            road_network_folder = os.path.join(
                self.base_path, "07_道路ネットワーク"
            )
            shp_files = self.__get_shapefiles(road_network_folder)

            if not shp_files:
                raise Exception(
                    "交通関連データ作成 道路ネットワークのShapefileが見つかりません。"
                )

            # ゾーンポリゴンを読み込む
            zones_layer = self.gpkg_manager.load_layer(
                'zones', None, withload_project=False
            )

            # is_target=1の市区町村のみを抽出
            target_zones_layer = processing.run(
                "native:extractbyattribute",
                {
                    'INPUT': zones_layer,
                    'FIELD': 'is_target',
                    'OPERATOR': 0,  # =
                    'VALUE': '1',
                    'OUTPUT': 'TEMPORARY_OUTPUT',
                },
            )['OUTPUT']

            # レイヤリストを作成
            layers = []
            required_fields = {
                "osm_id",
                "code",
                "fclass",
                "name",
                "ref",
                "oneway",
                "maxspeed",
                "layer",
                "bridge",
                "tunnel",
            }

            for shp_file in shp_files:
                # Shapefile読み込み
                layer = QgsVectorLayer(
                    shp_file, os.path.basename(shp_file), "ogr"
                )

                # レイヤの属性項目チェック
                layer_fields = set(layer.fields().names())
                if required_fields.issubset(layer_fields):
                    layers.append(layer)
                    # 取り込み対象のファイルパスをログ出力
                    msg = self.tr(
                        "Shapefile to be imported: %1"
                    ).replace("%1", shp_file)
                    QgsMessageLog.logMessage(
                        msg,
                        self.tr("Plugin"),
                        Qgis.Info,
                    )

                else:
                    data_name = self.tr("road network")
                    msg = (
                        self.tr("%1 cannot be loaded as %2 data.")
                        .replace("%1", shp_file)
                        .replace("%2", data_name)
                    )
                    QgsMessageLog.logMessage(
                        msg,
                        self.tr("Plugin"),
                        Qgis.Warning,
                    )

            if not layers:
                # 道路ネットワークのshpファイルが無い場合
                raise Exception(
                    "必要な道路ネットワークのShapefileが見つかりませんでした。"
                )

            merged_layer = self.__merge_layers(layers)

            processing.run("native:createspatialindex",
                           {'INPUT': merged_layer})

            processing.run("native:createspatialindex",
                           {'INPUT': target_zones_layer})

            # 選択された市区町村（is_target=1）の範囲と交差する道路のみを抽出
            extracted_layer = processing.run(
                "native:extractbylocation",
                {
                    'INPUT': merged_layer,
                    'PREDICATE': [0],  # intersects
                    'INTERSECT': target_zones_layer,
                    'OUTPUT': 'TEMPORARY_OUTPUT',
                },
            )['OUTPUT']

            # road_networksレイヤをGeoPackageに保存
            if not self.gpkg_manager.add_layer(
                extracted_layer, "road_networks", "道路ネットワーク"
            ):
                raise Exception(self.tr("Failed to add layer to GeoPackage."))

            data_name = self.tr("road network")
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
            # エラーメッセージをログに記録
            QgsMessageLog.logMessage(
                self.tr("An error occurred: %1").replace("%1", str(e)),
                self.tr("Plugin"),
                Qgis.Critical,
            )

            raise e

    def create_railway_stations(self):
        """鉄道駅位置データ作成"""
        try:
            # base_path 配下の「03_鉄道駅位置」フォルダを再帰的に探索してShapefileを収集
            railway_station_folder = os.path.join(self.base_path, "03_鉄道駅位置")
            shp_files = self.__get_shapefiles(railway_station_folder)

            if not shp_files:
                data_name = self.tr("railway station")
                msg = self.tr(
                    "The Shapefile for %1 was not found."
                ).replace("%1", data_name)
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Warning,
                )
                raise e

            # レイヤを格納するリスト
            layers = []

            for shp_file in shp_files:
                year = self.__extract_year_from_path(shp_file)
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

                # Shapefileの属性フィールドバリデーション
                layer_fields = set(layer.fields().names())
                required_fields = {
                    "N02_001",
                    "N02_002",
                    "N02_003",
                    "N02_004",
                    "N02_005",
                }  # 必須フィールド
                if not required_fields.issubset(layer_fields):
                    data_name = self.tr("railway station")
                    msg = (
                        self.tr("%1 cannot be loaded as %2 data.")
                        .replace("%1", shp_file)
                        .replace("%2", data_name)
                    )
                    QgsMessageLog.logMessage(
                        msg,
                        self.tr("Plugin"),
                        Qgis.Warning,
                    )
                    continue

                # 一時メモリレイヤを作成し、Shapefileのデータを取り込み
                temp_layer = QgsVectorLayer(
                    f"MultiLineString?crs={layer.crs().authid()}",
                    "railway_stations",
                    "memory",
                )
                temp_provider = temp_layer.dataProvider()

                # 必要なフィールドを追加
                fields = [
                    QgsField("type", QVariant.String),
                    QgsField("business_type", QVariant.String),
                    QgsField("railway_name", QVariant.String),
                    QgsField("company_name", QVariant.String),
                    QgsField("name", QVariant.String),
                    QgsField("code", QVariant.String),
                    QgsField("group_code", QVariant.String),
                    QgsField("year", QVariant.Int),
                ]
                temp_provider.addAttributes(fields)
                temp_layer.updateFields()

                temp_layer.startEditing()

                for feature in layer.getFeatures():
                    new_feature = QgsFeature()
                    new_feature.setGeometry(feature.geometry())

                    # 属性データ
                    attributes = [
                        feature["N02_001"],  # type
                        feature["N02_002"],  # business_type
                        feature["N02_003"],  # railway_name
                        feature["N02_004"],  # company_name
                        feature["N02_005"],  # name
                        (
                            feature["N02_005c"]
                            if "N02_005c" in layer_fields
                            else None
                        ),  # code
                        (
                            feature["N02_005g"]
                            if "N02_005g" in layer_fields
                            else None
                        ),  # group_code
                        year,  # year
                    ]
                    new_feature.setAttributes(attributes)
                    temp_provider.addFeature(new_feature)

                temp_layer.commitChanges()

                layers.append(temp_layer)

            if not layers:
                raise Exception(
                    "有効な鉄道駅位置データのShapefileが見つかりませんでした。"
                )

            # 複数のレイヤをマージ
            merged_layer = self.__merge_layers(layers)

            # railway_stationsレイヤをGeoPackageに保存
            if not self.gpkg_manager.add_layer(
                merged_layer, "railway_stations", "鉄道駅"
            ):
                raise Exception(self.tr("Failed to add layer to GeoPackage."))

            data_name = self.tr("railway station")
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

    def create_railway_networks(self):
        """鉄道ネットワークデータ作成"""
        try:
            # base_path 配下の「04_鉄道ネットワーク」フォルダを再帰的に探索してShapefileを収集
            railway_network_folder = os.path.join(
                self.base_path, "04_鉄道ネットワーク"
            )
            shp_files = self.__get_shapefiles(railway_network_folder)

            if not shp_files:
                data_name = self.tr("railway network")
                msg = self.tr(
                    "The Shapefile for %1 was not found."
                ).replace("%1", data_name)
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Warning,
                )
                raise e

            # レイヤを格納するリスト
            layers = []

            for shp_file in shp_files:
                year = self.__extract_year_from_path(shp_file)
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

                # Shapefileの属性フィールドバリデーション
                layer_fields = set(layer.fields().names())
                required_fields = {
                    "N02_001",
                    "N02_002",
                    "N02_003",
                    "N02_004",
                }  # 必須フィールド
                invalid_fields = (
                    layer_fields - required_fields
                )  # 必要なフィールド以外が含まれているかチェック
                if not required_fields.issubset(layer_fields) or invalid_fields:
                    data_name = self.tr("railway network")
                    msg = (
                        self.tr("%1 cannot be loaded as %2 data.")
                        .replace("%1", shp_file)
                        .replace("%2", data_name)
                    )
                    QgsMessageLog.logMessage(
                        msg,
                        self.tr("Plugin"),
                        Qgis.Warning,
                    )
                    continue

                # 一時メモリレイヤを作成し、Shapefileのデータを取り込み
                temp_layer = QgsVectorLayer(
                    f"MultiLineString?crs={layer.crs().authid()}",
                    "railway_networks",
                    "memory",
                )
                temp_provider = temp_layer.dataProvider()

                # 必要なフィールドを追加
                fields = [
                    QgsField("type", QVariant.String),
                    QgsField("business_type", QVariant.String),
                    QgsField("name", QVariant.String),
                    QgsField("company_name", QVariant.String),
                    QgsField("year", QVariant.Int),
                ]
                temp_provider.addAttributes(fields)
                temp_layer.updateFields()

                temp_layer.startEditing()

                for feature in layer.getFeatures():
                    new_feature = QgsFeature()
                    new_feature.setGeometry(feature.geometry())

                    # 属性データ
                    attributes = [
                        feature["N02_001"],  # type
                        feature["N02_002"],  # business_type
                        feature["N02_003"],  # name
                        feature["N02_004"],  # company_name
                        year,  # year
                    ]
                    new_feature.setAttributes(attributes)
                    temp_provider.addFeature(new_feature)

                temp_layer.commitChanges()

                layers.append(temp_layer)

            if not layers:
                raise Exception(
                    "有効な鉄道ネットワークのShapefileが見つかりませんでした。"
                )

            # 複数のレイヤをマージ
            merged_layer = self.__merge_layers(layers)

            # railway_networksレイヤをGeoPackageに保存
            if not self.gpkg_manager.add_layer(
                merged_layer, "railway_networks", "鉄道ネットワーク"
            ):
                raise Exception(self.tr("Failed to add layer to GeoPackage."))

            data_name = self.tr("railway network")
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

    def create_bus_networks(self):
        """バスネットワークデータ作成"""
        try:
            # base_path 配下の「06_バスルート」フォルダを再帰的に探索してShapefileを収集
            bus_route_folder = os.path.join(self.base_path, "06_バスルート")
            shp_files = self.__get_shapefiles(bus_route_folder)

            if not shp_files:
                data_name = self.tr("bus route Shapefile")
                msg = self.tr(
                    "The %1 was not found."
                ).replace("%1", data_name)
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Warning,
                )
                raise e

            # バスルートレイヤを格納するリスト
            layers = []

            # 年度を取得（フォルダ名から）
            year = self.__extract_year_from_path(bus_route_folder)

            for shp_file in shp_files:
                if self.check_canceled():
                    return  # キャンセルチェック

                # Shapefile 読み込み
                layer = QgsVectorLayer(
                    shp_file, os.path.basename(shp_file), "ogr"
                )

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

                # Shapefileの属性フィールドバリデーション
                layer_fields = set(layer.fields().names())
                required_fields = {
                    "N07_001",  # 事業者名
                }  # 必須フィールド（N07_001のみ必須とする）

                if not required_fields.issubset(layer_fields):
                    data_name = self.tr("bus network")
                    msg = (
                        self.tr("%1 cannot be loaded as %2 data.")
                        .replace("%1", shp_file)
                        .replace("%2", data_name)
                    )
                    QgsMessageLog.logMessage(
                        msg,
                        self.tr("Plugin"),
                        Qgis.Warning,
                    )
                    continue

                # 一時メモリレイヤを作成し、Shapefileのデータを取り込み
                temp_layer = QgsVectorLayer(
                    f"MultiLineString?crs={layer.crs().authid()}", "bus_networks", "memory"
                )
                temp_provider = temp_layer.dataProvider()

                # 必要なフィールドを追加
                fields = [
                    QgsField("operator_name", QVariant.String),  # N07_001
                    QgsField("remarks", QVariant.String),        # N07_002
                    QgsField("year", QVariant.Int),
                ]
                temp_provider.addAttributes(fields)
                temp_layer.updateFields()

                temp_layer.startEditing()

                # フィーチャの追加
                for feature in layer.getFeatures():
                    new_feature = QgsFeature()
                    new_feature.setGeometry(feature.geometry())

                    # 属性データ
                    attributes = [
                        feature["N07_001"],  # operator_name（事業者名）
                        feature.attribute("N07_002") if "N07_002" in layer_fields else "",  # remarks（備考）
                        year,  # year
                    ]
                    new_feature.setAttributes(attributes)
                    temp_provider.addFeature(new_feature)

                temp_layer.commitChanges()

                layers.append(temp_layer)

            if not layers:
                raise Exception(
                    "有効なバスルートのShapefileが見つかりませんでした。"
                )

            # 複数のレイヤをマージ
            merged_layer = self.__merge_layers(layers)

            # bus_networksレイヤをGeoPackageに保存
            if not self.gpkg_manager.add_layer(
                merged_layer, "bus_networks", "バスネットワーク"
            ):
                raise Exception(self.tr("Failed to add layer to GeoPackage."))

            data_name = self.tr("bus network")
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

    def create_bus_stops(self):
        """バス停データ作成"""
        try:
            # base_path 配下の「05_バス停」フォルダを再帰的に探索してShapefileを収集
            bus_stop_folder = os.path.join(self.base_path, "05_バス停")
            shp_files = self.__get_shapefiles(bus_stop_folder)

            if not shp_files:
                data_name = self.tr("bus stop Shapefile")
                msg = self.tr(
                    "The %1 was not found."
                ).replace("%1", data_name)
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Warning,
                )
                raise e

            # バス停レイヤを格納するリスト
            layers = []

            # 年度を取得（フォルダ名から）
            year = self.__extract_year_from_path(bus_stop_folder)

            for shp_file in shp_files:
                if self.check_canceled():
                    return  # キャンセルチェック

                # Shapefile 読み込み
                layer = QgsVectorLayer(
                    shp_file, os.path.basename(shp_file), "ogr"
                )

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

                # Shapefileの属性フィールドバリデーション
                layer_fields = set(layer.fields().names())
                required_fields = {
                    "P11_001",  # バス停名
                    "P11_002",  # バス事業者名
                }  # 必須フィールド

                if not required_fields.issubset(layer_fields):
                    data_name = self.tr("bus stop")
                    msg = (
                        self.tr("%1 cannot be loaded as %2 data.")
                        .replace("%1", shp_file)
                        .replace("%2", data_name)
                    )
                    QgsMessageLog.logMessage(
                        msg,
                        self.tr("Plugin"),
                        Qgis.Warning,
                    )
                    continue

                # 一時メモリレイヤを作成し、Shapefileのデータを取り込み
                temp_layer = QgsVectorLayer(
                    f"Point?crs={layer.crs().authid()}", "bus_stops", "memory"
                )
                temp_provider = temp_layer.dataProvider()

                # 必要なフィールドを追加
                fields = [
                    QgsField("stop_name", QVariant.String),      # P11_001
                    QgsField("operator_name", QVariant.String),  # P11_002
                    QgsField("remarks", QVariant.String),        # P11_005
                    QgsField("year", QVariant.Int),
                ]
                temp_provider.addAttributes(fields)
                temp_layer.updateFields()

                temp_layer.startEditing()

                # フィーチャの追加
                for feature in layer.getFeatures():
                    new_feature = QgsFeature()
                    new_feature.setGeometry(feature.geometry())

                    # 属性データ
                    attributes = [
                        feature["P11_001"],  # stop_name（バス停名）
                        feature["P11_002"],  # operator_name（バス事業者名）
                        feature.attribute("P11_005") if "P11_005" in layer_fields else "",  # remarks（備考）
                        year,  # year
                    ]
                    new_feature.setAttributes(attributes)
                    temp_provider.addFeature(new_feature)

                temp_layer.commitChanges()

                layers.append(temp_layer)

            if not layers:
                raise Exception(
                    "有効なバス停のShapefileが見つかりませんでした。"
                )

            # 複数のレイヤをマージ
            merged_layer = self.__merge_layers(layers)

            # bus_stopsレイヤをGeoPackageに保存
            if not self.gpkg_manager.add_layer(
                merged_layer, "bus_stops", "バス停"
            ):
                raise Exception(self.tr("Failed to add layer to GeoPackage."))

            data_name = self.tr("bus stop")
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

    def __extract_year_from_path(self, file_path):
        """ファイルパスから年度を抽出"""
        try:
            match = re.search(r'(\d{4})年', file_path)
            if match:
                return int(match.group(1))
            else:
                msg = self.tr(
                    "Failed to extract year from file path: %1"
                ).replace(
                    "%1", file_path
                )
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Warning,
                )
                return None
        except Exception as e:
            msg = self.tr(
                "An error occurred during year extraction: %1"
            ).replace(
                "%1", e
            )
            QgsMessageLog.logMessage(
                msg,
                self.tr("Plugin"),
                Qgis.Critical,
            )
            return None

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

