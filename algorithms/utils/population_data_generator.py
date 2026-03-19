"""
/***************************************************************************
 *
 * 【FN003】人口データ作成機能
 *
 ***************************************************************************/
"""

import os
import csv
import re

import chardet
import processing
from qgis.core import (
    QgsMessageLog,
    Qgis,
    QgsVectorLayer,
    QgsFeatureRequest,
    QgsField,
    QgsFeature,
    QgsSpatialIndex,
)
from PyQt5.QtCore import QCoreApplication, QVariant
from PyQt5.QtWidgets import QApplication

from .gpkg_manager import GpkgManager
from ...models.population import PopulationModel

class PopulationDataGenerator:
    """人口データ取り込み・作成"""
    def __init__(self, base_path, check_canceled_callback=None, gpkg_manager=None):
        # GeoPackageマネージャーを初期化
        self.gpkg_manager = gpkg_manager
        # インプットデータパス
        self.base_path = base_path

        self.check_canceled = check_canceled_callback

    def tr(self, message):
        """翻訳用のメソッド"""
        return QCoreApplication.translate(self.__class__.__name__, message)

    def load_population_meshes(self):
        """人口データ・メッシュデータ読み込み"""
        try:
            # base_path 配下の「10_250mメッシュ」フォルダを再帰的に探索してShapefileを収集
            mesh_folder = os.path.join(self.base_path, "10_250mメッシュ")
            shp_files = self._get_shapefiles(mesh_folder)

            if not shp_files:
                raise Exception(
                    "人口データ作成 250mメッシュのShapefileが見つかりません。"
                )

            # レイヤリストを作成
            layers = []
            for shp_file in shp_files:
                # Shapefile読み込み
                layer = QgsVectorLayer(
                    shp_file, os.path.basename(shp_file), "ogr"
                )
                if not layer.isValid():
                    raise Exception(
                        f"Shapefileレイヤの読み込みに失敗しました: {shp_file}"
                    )

                # レイヤをリストに追加
                layers.append(layer)

            # レイヤをマージ
            layer = self.merge_layers(layers)

            # 列名を変更（フィールド名を指定された形式に変換）
            self.rename_fields(
                layer,
                {
                    "KEY_CODE": "key_code",
                    "MESH1_ID": "mesh1_id",
                    "MESH2_ID": "mesh2_id",
                    "MESH3_ID": "mesh3_id",
                    "MESH4_ID": "mesh4_id",
                    "MESH5_ID": "mesh5_id",
                    "OBJ_ID": "obj_id",
                },
            )

            if self.check_canceled():
                return  # キャンセルチェック

            # ゾーンポリゴンを取得
            zones_layer = self.gpkg_manager.load_layer(
                'zones', None, withload_project=False
            )

            # ゾーンポリゴン範囲（交差する）のメッシュに絞り込み
            layer = self.__extract(layer, zones_layer)

            # 人口データを追加
            population_folder = os.path.join(self.base_path, "11_250mメッシュ人口")
            population_data = self.collect_population_data(population_folder)
            layer = self.add_population_data(layer, population_data)

            if self.check_canceled():
                return  # キャンセルチェック

            # 集計対象区域内の人口データを追加
            layer = self.add_target_area_population(layer, zones_layer, population_data)

            if self.check_canceled():
                return  # キャンセルチェック

            # 人口目標設定データ取り込み
            # population_target_setting.csv を読み込む
            csv_path = os.path.join(
                self.base_path, "population_target_setting.csv"
            )
            if not os.path.exists(csv_path):
                raise Exception(
                    self.tr("%1 was not found.").replace("%1", csv_path)
                )


            # population_target_settings 一時レイヤを作成
            target_layer = QgsVectorLayer(
                "None", "population_target_settings", "memory"
            )
            provider = target_layer.dataProvider()

            # フィールド定義 (comparative_year と target_population)
            provider.addAttributes(
                [
                    QgsField("comparative_year", QVariant.Int),
                    QgsField("target_population", QVariant.Double),
                ]
            )
            target_layer.updateFields()

            # population_target_setting.csv を読み込んでフィーチャを追加
            with open(csv_path, 'r', encoding='shift_jis') as file:
                next(file)  # ヘッダーをスキップ
                for line in file:
                    year, population = line.strip().split(',')
                    feature = QgsFeature()
                    feature.setFields(target_layer.fields())
                    feature.setAttribute("comparative_year", int(year))
                    feature.setAttribute(
                        "target_population", float(population))
                    provider.addFeature(feature)

            if target_layer.featureCount() <= 0:
                raise Exception(
                    "population_target_setting.csv に比較将来年度、目標人口を指定してください"
                )

            # population_target_settings レイヤを GeoPackage に保存
            if not self.gpkg_manager.add_layer(
                target_layer, "population_target_settings", None, False
            ):
                raise Exception(
                    "GeoPackageへの population_target_settings レイヤ追加に失敗しました。"
                )

            # 将来推定人口データを読み込み（zonesで絞り込み）
            future_population_layer = self.load_future_population(zones_layer)
            # 将来推定人口データをメッシュデータに付与
            self.add_future_population_data(layer, future_population_layer)

            if self.check_canceled():
                return  # キャンセルチェック

            # 集計対象区域内の将来推計人口データを追加
            self.add_target_area_future_population(layer, zones_layer)

            # 追加属性（人口増減、人口増減の変化、将来の人口増減）
            self.calculate_population_metrics(layer)

            if self.check_canceled():
                return  # キャンセルチェック

            # meshesレイヤをGeoPackageに保存
            if not self.gpkg_manager.add_layer(layer, "meshes", "人口メッシュ"):
                raise Exception(self.tr("Failed to add layer to GeoPackage."))

            data_name = self.tr("meshes")
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

    def _get_shapefiles(self, directory):
        """指定されたディレクトリ配下のすべてのShapefile (.shp) を再帰的に取得する"""
        shp_files = []
        for root, _, files in os.walk(directory):
            for file in files:
                if file.endswith(".shp"):
                    shp_files.append(os.path.join(root, file))
        return shp_files

    def rename_fields(self, layer, field_mapping):
        """指定されたフィールドマッピングを基にレイヤ内のフィールド名を変更する"""
        provider = layer.dataProvider()
        for old_name, new_name in field_mapping.items():
            field_index = layer.fields().indexOf(old_name)
            if field_index != -1:
                # 指定されたフィールド名を新しい名前に変更
                provider.renameAttributes({field_index: new_name})
        layer.updateFields()

    def collect_population_data(self, base_path):
        """指定されたディレクトリ配下のすべての年度フォルダから人口データを再帰的に収集する"""
        population_data = []

        def find_txt_files(directory):
            txt_files = []
            for root, _, files in os.walk(directory):
                for file in files:
                    if file.endswith(".txt"):
                        file_path = os.path.join(root, file)
                        txt_files.append(file_path)
            return txt_files

        for year_folder in os.listdir(base_path):
            if self.check_canceled():
                return  # キャンセルチェック
            year_path = os.path.join(base_path, year_folder)
            if os.path.isdir(year_path):
                year = int(year_folder.replace('年', ''))
                msg = self.tr(
                    "Population data creation for year: %1"
                ).replace("%1", str(year))
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Info,
                )

                txt_files = find_txt_files(year_path)

                for file_path in txt_files:
                    # ファイルエンコード検出
                    with open(file_path, 'rb') as f:
                        raw_data = f.read()
                        detected_encoding = chardet.detect(raw_data)['encoding']
                        msg = self.tr(
                            "Population data creation - Detected encoding: %1 "
                            "for file: %2"
                        ).replace(
                            "%1", detected_encoding
                        ).replace(
                            "%2", file_path
                        )

                        QgsMessageLog.logMessage(
                            msg,
                            self.tr("Plugin"),
                            Qgis.Info,
                        )

                    if detected_encoding is None:
                        msg = self.tr(
                            "Encoding detection error for file: %1. "
                            "Attempting to detect using an alternative method."
                        ).replace("%1", file_path)
                        QgsMessageLog.logMessage(
                            msg,
                            self.tr("Plugin"),
                            Qgis.Warning,
                        )
                        detected_encoding = self.__detect_encoding(file_path)

                    msg = self.tr(
                        "Population data creation - Detected encoding: %1 "
                        "for file: %2"
                    ).replace("%1", detected_encoding).replace("%2", file_path)
                    QgsMessageLog.logMessage(
                        msg,
                        self.tr("Plugin"),
                        Qgis.Info,
                    )

                    # ファイルをエンコードに基づいて開く
                    with open(
                        file_path, newline='', encoding=detected_encoding
                    ) as f:
                        reader = csv.reader(f)

                        # 1行目と2行目のヘッダーを取得してスキップ
                        header = next(reader)  # 1行目
                        next(reader)  # 2行目

                        for row in reader:
                            row_data = {
                                col: val for col, val in zip(header, row)
                            }

                            # 数値型に変換
                            for key, val in row_data.items():
                                try:
                                    row_data[key] = int(val)
                                except ValueError:
                                    row_data[key] = 0

                            # モデル定義にてパース
                            parsed_data = PopulationModel.parse(year, row_data)
                            if parsed_data:
                                # 既にその年度が存在するか確認
                                year_entry = next(
                                    (
                                        entry
                                        for entry in population_data
                                        if entry['year'] == year
                                    ),
                                    None,
                                )

                                if year_entry:
                                    # 既存の年度にデータを追加
                                    year_entry['data'].append(parsed_data)
                                else:
                                    # 新しい年度のエントリーを追加
                                    population_data.append(
                                        {"year": year, "data": [parsed_data]}
                                    )

        return population_data

    def add_population_data(self, layer, population_data):
        """人口データをmeshesレイヤに追加する"""

        # 年度ごとのデータを一時レイヤとして作成しJOIN
        for year_data in population_data:
            if self.check_canceled():
                return  # キャンセルチェック
            year = year_data['year']

            # 一時レイヤを作成し、レイヤ名に年度を含める
            temp_layer = QgsVectorLayer("None", f"temp_data_{year}", "memory")
            provider = temp_layer.dataProvider()

            # フィールド定義
            fields = [QgsField("key_code", QVariant.String)]
            for attr in PopulationModel.attributes:
                if attr != "key_code":
                    year_attr = f"{year}_{attr}"
                    fields.append(QgsField(year_attr, QVariant.Int))
                    if attr == "population":
                        fields.append(
                            QgsField(f"{year}_rank", QVariant.Double))
            provider.addAttributes(fields)
            temp_layer.updateFields()

            # データをフィーチャとして追加
            for data in year_data['data']:
                feature = QgsFeature()
                feature.setFields(temp_layer.fields())
                feature.setAttribute("key_code", data['key_code'])
                for attr in PopulationModel.attributes:
                    if attr != "key_code":
                        year_attr = f"{year}_{attr}"
                        if attr == "population":
                            population = data.get(attr, 0)
                            # メッシュ人口密度（人口/6.25ha）を計算して rank フィールドに追加
                            population_density = (
                                population / 6.25
                            )  # メッシュの面積は6.25ha
                            feature.setAttribute(
                                f"{year}_rank", population_density
                            )
                        feature.setAttribute(year_attr, data.get(attr, 0))
                provider.addFeature(feature)

            # 人口データレイヤをJOIN
            layer = self.join_layers(layer, temp_layer, "key_code", "key_code")

        return layer

    def add_target_area_population(self, layer, zones_layer, population_data):
        """集計対象区域(is_target=1)内の人口データを計算してmeshesレイヤに追加する"""
        try:
            from qgis.core import (
                QgsCoordinateTransform,
                QgsProject,
                QgsCoordinateReferenceSystem,
                QgsGeometry,
            )

            provider = layer.dataProvider()

            # 年度リストを取得
            years = [year_data['year'] for year_data in population_data]

            # 各年度に対応するフィールドを追加
            existing_field_names = [field.name() for field in layer.fields()]
            for year in years:
                field_name = f"{year}_target_area_population"
                if field_name not in existing_field_names:
                    provider.addAttributes(
                        [QgsField(field_name, QVariant.Double)]
                    )
            layer.updateFields()

            # is_target=1のゾーンのみを抽出（int/str両対応）
            target_zone_ids = []
            for f in zones_layer.getFeatures():
                is_target_val = f['is_target']
                # 数値の1、文字列の"1"、Trueのいずれかに対応
                if is_target_val == 1 or is_target_val == "1" or is_target_val is True:
                    target_zone_ids.append(f.id())

            if not target_zone_ids:
                QgsMessageLog.logMessage(
                    self.tr("No target zones (is_target=1) found. Skipping target area population calculation."),
                    self.tr("Plugin"),
                    Qgis.Warning,
                )
                return layer

            QgsMessageLog.logMessage(
                self.tr("Calculating target area population for %1 target zones...").replace("%1", str(len(target_zone_ids))),
                self.tr("Plugin"),
                Qgis.Info,
            )

            # Processing APIを使ってゾーンをメッシュと同じCRSに再投影
            mesh_crs = layer.crs()

            # is_target=1のゾーンのみを抽出してメモリレイヤを作成
            target_zones_layer = processing.run(
                "native:extractbyexpression",
                {
                    'INPUT': zones_layer,
                    'EXPRESSION': '"is_target" = 1 OR "is_target" = \'1\'',
                    'OUTPUT': 'memory:'
                }
            )['OUTPUT']

            # ゾーンをメッシュと同じCRSに再投影
            reprojected_zones = processing.run(
                "native:reprojectlayer",
                {
                    'INPUT': target_zones_layer,
                    'TARGET_CRS': mesh_crs,
                    'OUTPUT': 'memory:'
                }
            )['OUTPUT']

            # 再投影したゾーンを1つのジオメトリに結合
            target_zone_geoms = []
            for zone_feature in reprojected_zones.getFeatures():
                target_zone_geoms.append(QgsGeometry(zone_feature.geometry()))

            combined_target_geom = QgsGeometry.unaryUnion(target_zone_geoms)

            # 面積計算用のCRSに変換（メッシュが地理座標系の場合）
            if mesh_crs.isGeographic():
                # 面積計算用にEPSG:6677（JGD2011 / Japan Plane Rectangular CS IX）を使用
                area_crs = QgsCoordinateReferenceSystem("EPSG:6677")
                transform_to_area = QgsCoordinateTransform(
                    mesh_crs, area_crs, QgsProject.instance()
                )
                use_projection = True

                # combined_target_geomを投影座標系に変換
                combined_target_geom.transform(transform_to_area)
            else:
                use_projection = False
                transform_to_area = None

            # バッチ処理用の準備
            attribute_changes = {}
            processed_count = 0
            total_features = layer.featureCount()

            for feature in layer.getFeatures():
                if self.check_canceled():
                    return  # キャンセルチェック

                mesh_geom = QgsGeometry(feature.geometry())

                # 面積計算用に投影座標系に変換
                if use_projection:
                    mesh_geom.transform(transform_to_area)

                # メッシュ全体の面積
                mesh_area = mesh_geom.area()

                if mesh_area <= 0:
                    processed_count += 1
                    continue

                # 結合した対象ゾーンとの交差面積を計算
                intersection_geom = mesh_geom.intersection(combined_target_geom)
                intersection_area = intersection_geom.area() if intersection_geom and not intersection_geom.isEmpty() else 0

                # 面積割合を計算
                area_ratio = intersection_area / mesh_area if mesh_area > 0 else 0

                # 各年度の target_area_population を計算
                for year in years:
                    population_field = f"{year}_population"
                    target_field = f"{year}_target_area_population"

                    population = feature[population_field]
                    if population is not None and isinstance(population, (int, float)):
                        target_area_population = population * area_ratio
                    else:
                        target_area_population = 0

                    attribute_changes.setdefault(
                        feature.id(), {}
                    ).update(
                        {
                            layer.fields().indexFromName(target_field): round(target_area_population, 2)
                        }
                    )

                processed_count += 1
                if processed_count % 1000 == 0:
                    QgsMessageLog.logMessage(
                        self.tr("Target area population calculation: %1/%2 features processed...").replace("%1", str(processed_count)).replace("%2", str(total_features)),
                        self.tr("Plugin"),
                        Qgis.Info,
                    )
                    QApplication.processEvents()

            # 一括でコミット
            if attribute_changes:
                layer.startEditing()
                provider.changeAttributeValues(attribute_changes)
                layer.commitChanges()

                QgsMessageLog.logMessage(
                    self.tr("Target area population calculation completed. Updated %1 features.").replace("%1", str(len(attribute_changes))),
                    self.tr("Plugin"),
                    Qgis.Info,
                )
            else:
                QgsMessageLog.logMessage(
                    self.tr("Warning: No features were updated with target area population data"),
                    self.tr("Plugin"),
                    Qgis.Warning,
                )

            return layer

        except Exception as e:
            QgsMessageLog.logMessage(
                self.tr("An error occurred during target area population calculation: %1").replace("%1", str(e)),
                self.tr("Plugin"),
                Qgis.Critical,
            )
            raise e

    def add_target_area_future_population(self, layer, zones_layer):
        """集計対象区域(is_target=1)内の将来推計人口データを計算してmeshesレイヤに追加する"""
        try:
            from qgis.core import (
                QgsCoordinateTransform,
                QgsProject,
                QgsCoordinateReferenceSystem,
                QgsGeometry,
            )

            provider = layer.dataProvider()

            # future_yyyy_XXX 形式のフィールドを取得
            future_field_pattern = re.compile(r'^future_\d{4}_\w+$')
            future_fields = [
                field.name() for field in layer.fields()
                if future_field_pattern.match(field.name())
            ]

            if not future_fields:
                QgsMessageLog.logMessage(
                    self.tr("No future population fields found. Skipping target area calculation."),
                    self.tr("Plugin"),
                    Qgis.Warning,
                )
                return layer

            # 各フィールドに対応するtarget_areaフィールドを追加
            # future_2030_PTN -> future_2030_target_area_PTN
            existing_field_names = [field.name() for field in layer.fields()]
            target_area_field_map = {}
            for field_name in future_fields:
                target_field = re.sub(
                    r'^(future_\d{4})_(\w+)$',
                    r'\1_target_area_\2',
                    field_name
                )
                target_area_field_map[field_name] = target_field
                if target_field not in existing_field_names:
                    provider.addAttributes(
                        [QgsField(target_field, QVariant.Double)]
                    )
            layer.updateFields()

            # is_target=1のゾーンのみを抽出（int/str両対応）
            target_zone_ids = []
            for f in zones_layer.getFeatures():
                is_target_val = f['is_target']
                if is_target_val == 1 or is_target_val == "1" or is_target_val is True:
                    target_zone_ids.append(f.id())

            if not target_zone_ids:
                QgsMessageLog.logMessage(
                    self.tr("No target zones (is_target=1) found. Skipping future target area population calculation."),
                    self.tr("Plugin"),
                    Qgis.Warning,
                )
                return layer

            QgsMessageLog.logMessage(
                self.tr("Calculating future target area population for %1 target zones...").replace("%1", str(len(target_zone_ids))),
                self.tr("Plugin"),
                Qgis.Info,
            )

            # Processing APIを使ってゾーンをメッシュと同じCRSに再投影
            mesh_crs = layer.crs()

            # is_target=1のゾーンのみを抽出してメモリレイヤを作成
            target_zones_layer = processing.run(
                "native:extractbyexpression",
                {
                    'INPUT': zones_layer,
                    'EXPRESSION': '"is_target" = 1 OR "is_target" = \'1\'',
                    'OUTPUT': 'memory:'
                }
            )['OUTPUT']

            # ゾーンをメッシュと同じCRSに再投影
            reprojected_zones = processing.run(
                "native:reprojectlayer",
                {
                    'INPUT': target_zones_layer,
                    'TARGET_CRS': mesh_crs,
                    'OUTPUT': 'memory:'
                }
            )['OUTPUT']

            # 再投影したゾーンを1つのジオメトリに結合
            target_zone_geoms = []
            for zone_feature in reprojected_zones.getFeatures():
                target_zone_geoms.append(QgsGeometry(zone_feature.geometry()))

            combined_target_geom = QgsGeometry.unaryUnion(target_zone_geoms)

            # 面積計算用のCRSに変換（メッシュが地理座標系の場合）
            if mesh_crs.isGeographic():
                area_crs = QgsCoordinateReferenceSystem("EPSG:6677")
                transform_to_area = QgsCoordinateTransform(
                    mesh_crs, area_crs, QgsProject.instance()
                )
                use_projection = True

                combined_target_geom.transform(transform_to_area)
            else:
                use_projection = False
                transform_to_area = None

            # バッチ処理用の準備
            attribute_changes = {}
            processed_count = 0
            total_features = layer.featureCount()

            for feature in layer.getFeatures():
                if self.check_canceled():
                    return  # キャンセルチェック

                mesh_geom = QgsGeometry(feature.geometry())

                # 面積計算用に投影座標系に変換
                if use_projection:
                    mesh_geom.transform(transform_to_area)

                # メッシュ全体の面積
                mesh_area = mesh_geom.area()

                if mesh_area <= 0:
                    processed_count += 1
                    continue

                # 結合した対象ゾーンとの交差面積を計算
                intersection_geom = mesh_geom.intersection(combined_target_geom)
                intersection_area = intersection_geom.area() if intersection_geom and not intersection_geom.isEmpty() else 0

                # 面積割合を計算
                area_ratio = intersection_area / mesh_area if mesh_area > 0 else 0

                # 各将来推計人口フィールドの target_area 値を計算
                for field_name, target_field in target_area_field_map.items():
                    value = feature[field_name]
                    if value is not None and isinstance(value, (int, float)):
                        target_area_value = value * area_ratio
                    else:
                        target_area_value = 0

                    attribute_changes.setdefault(
                        feature.id(), {}
                    ).update(
                        {
                            layer.fields().indexFromName(target_field): round(target_area_value, 6)
                        }
                    )

                processed_count += 1
                if processed_count % 1000 == 0:
                    QgsMessageLog.logMessage(
                        self.tr("Future target area population calculation: %1/%2 features processed...").replace("%1", str(processed_count)).replace("%2", str(total_features)),
                        self.tr("Plugin"),
                        Qgis.Info,
                    )
                    QApplication.processEvents()

            # 一括でコミット
            if attribute_changes:
                layer.startEditing()
                provider.changeAttributeValues(attribute_changes)
                layer.commitChanges()

                QgsMessageLog.logMessage(
                    self.tr("Future target area population calculation completed. Updated %1 features.").replace("%1", str(len(attribute_changes))),
                    self.tr("Plugin"),
                    Qgis.Info,
                )
            else:
                QgsMessageLog.logMessage(
                    self.tr("Warning: No features were updated with future target area population data"),
                    self.tr("Plugin"),
                    Qgis.Warning,
                )

            return layer

        except Exception as e:
            QgsMessageLog.logMessage(
                self.tr("An error occurred during future target area population calculation: %1").replace("%1", str(e)),
                self.tr("Plugin"),
                Qgis.Critical,
            )
            raise e

    def join_layers(self, target_layer, join_layer, target_field, join_field):
        """レイヤ結合"""
        result = processing.run(
            "native:joinattributestable",
            {
                'INPUT': target_layer,
                'FIELD': target_field,
                'INPUT_2': join_layer,
                'FIELD_2': join_field,
                'FIELDS_TO_COPY': join_layer.fields().names(),
                'METHOD': 0,  # LeftJoin
                'DISCARD_NONMATCHING': False,  # 一致しないレコードも保持する
                'PREFIX': '',
                'OUTPUT': 'memory:',
            },
        )

        return result['OUTPUT']

    def load_future_population(self, zones_layer=None):
        """将来人口データ読み込み"""
        try:
            # base_path配下の「12_500mメッシュ別将来人口」フォルダを再帰的に探索してShapefileを収集
            future_population_folder = os.path.join(
                self.base_path, "12_500mメッシュ別将来人口"
            )
            shp_files = self._get_shapefiles(future_population_folder)

            if not shp_files:
                data_name = self.tr("future population")
                raise Exception(self.tr("The Shapefile for %1 was not found.")
                                .replace("%1", data_name))

            # レイヤリストを作成
            layers = []
            for shp_file in shp_files:
                if self.check_canceled():
                    return  # キャンセルチェック
                # Shapefileロード
                layer = QgsVectorLayer(
                    shp_file, os.path.basename(shp_file), "ogr"
                )
                if not layer.isValid():
                    raise Exception(
                        f"Shapefileレイヤの読み込みに失敗しました: {shp_file}"
                    )

                # レイヤをリストに追加
                layers.append(layer)

            # レイヤをマージ
            merged_layer = self.merge_layers(layers)

            # zones_layerが指定されている場合、zonesの範囲で絞り込む
            if zones_layer:
                merged_layer = self.__extract(merged_layer, zones_layer)
                msg = self.tr("Future population layer filtered by zones.")
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Info,
                )

            # マージしたレイヤをGeoPackageに保存
            if not self.gpkg_manager.add_layer(
                merged_layer, "future_population", "将来推計人口メッシュ"
            ):
                raise Exception(self.tr("Failed to add layer to GeoPackage."))

            data_name = self.tr("future population")
            msg = self.tr(
                "%1 data generation completed."
            ).replace("%1", data_name)
            QgsMessageLog.logMessage(
                msg,
                self.tr("Plugin"),
                Qgis.Info,
            )
            return merged_layer

        except Exception as e:
            # エラーメッセージをログに記録
            QgsMessageLog.logMessage(
                self.tr("An error occurred: %1").replace("%1", str(e)),
                self.tr("Plugin"),
                Qgis.Critical,
            )
            return False

    def merge_layers(self, layers):
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

    def add_future_population_data(self, layer, future_population_layer):
        """将来推定人口データを250mメッシュレイヤに追加する"""
        try:
            provider = layer.dataProvider()

            # メッシュIDをキーとした辞書を事前に作成
            # 500mメッシュ（前8桁）ごとにグループ化
            QgsMessageLog.logMessage(
                self.tr("Creating mesh ID index for fast lookup..."),
                self.tr("Plugin"),
                Qgis.Info,
            )
            mesh_feature_dict = {}  # 250mメッシュIDをキーとする辞書
            mesh_500m_dict = {}  # 500mメッシュID（前8桁）をキーとする辞書

            for feature in layer.getFeatures():
                mesh_id = (
                    str(feature["mesh1_id"]) +
                    str(feature["mesh2_id"]) +
                    str(feature["mesh3_id"]) +
                    str(feature["mesh4_id"])
                )

                # 250mメッシュ辞書に追加
                if mesh_id not in mesh_feature_dict:
                    mesh_feature_dict[mesh_id] = []
                mesh_feature_dict[mesh_id].append(feature)

                # 250mメッシュ辞書に追加（前9桁をキーとして: mesh1~mesh4）
                mesh_500m_id = mesh_id[:9]
                if mesh_500m_id not in mesh_500m_dict:
                    mesh_500m_dict[mesh_500m_id] = []
                mesh_500m_dict[mesh_500m_id].append(feature)

            population_target_settings_layer = self.gpkg_manager.load_layer(
                'population_target_settings', None, withload_project=False
            )

            future_years = []
            for feature in population_target_settings_layer.getFeatures():
                comparative_year = feature['comparative_year']
                if comparative_year is not None:
                    future_years.append(str(comparative_year))

            future_attributes = [
                'PTN',
                'PT00',
                'PT01',
                'PT02',
                'PT03',
                'PT04',
                'PT05',
                'PT06',
                'PT07',
                'PT08',
                'PT09',
                'PT10',
                'PT11',
                'PT12',
                'PT13',
                'PT14',
                'PT15',
                'PT16',
                'PT17',
                'PT18',
                'PT19',
                'PT20',
                'PTA',
                'PTB',
                'PTC',
                'PTD',
                'PTE',
            ]

            # レイヤに既に存在するフィールドを取得
            existing_field_names = [field.name() for field in layer.fields()]

            # 各年度・属性に対応するフィールドを追加
            for year in future_years:
                for attr in future_attributes:
                    field_name = f"future_{year}_{attr}"
                    if field_name not in existing_field_names:
                        provider.addAttributes(
                            [QgsField(field_name, QVariant.Double)]
                        )

            # フィールド追加更新
            layer.updateFields()

            # 最新年度の population キー
            latest_year = max(PopulationModel.year_mappings.keys())

            # バッチ処理用の準備
            attribute_changes = {}
            processed_count = 0
            matched_count = 0
            skipped_count = 0
            total_features = future_population_layer.featureCount()

            QgsMessageLog.logMessage(
                self.tr("Processing %1 future population features...").replace("%1", str(total_features)),
                self.tr("Plugin"),
                Qgis.Info,
            )

            for future_feature in future_population_layer.getFeatures():
                if self.check_canceled():
                    return  # キャンセルチェック

                future_mesh_id = future_feature["MESH_ID"]

                # 250mメッシュIDに対応する250mメッシュを検索
                prefix = str(future_mesh_id)
                matching_features = mesh_500m_dict.get(prefix, [])

                # 該当がない場合はスキップ
                if not matching_features:
                    processed_count += 1
                    skipped_count += 1
                    if processed_count % 1000 == 0:
                        QgsMessageLog.logMessage(
                            self.tr("Processed %1/%2 features (matched: %3, skipped: %4)...").replace("%1", str(processed_count)).replace("%2", str(total_features)).replace("%3", str(matched_count)).replace("%4", str(skipped_count)),
                            self.tr("Plugin"),
                            Qgis.Info,
                        )
                        QApplication.processEvents()
                    continue

                total_population = 0
                known_population_map = []
                unknown_features = []

                # 対象の250mメッシュの最新人口合計を計算
                for match_feature in matching_features:
                    pop = match_feature[
                        f"{latest_year}_population"
                    ]  # 最新の年度の人口
                    if pop and pop not in ('*', ''):
                        total_population += float(pop)
                        known_population_map.append(
                            (match_feature.id(), float(pop))
                        )
                    else:
                        unknown_features.append(match_feature)

                # 全ての250mメッシュの人口が不明な場合、4等分して均等に分割
                if total_population == 0:
                    split_value = 4  # 常に4等分
                    for i, match_feature in enumerate(matching_features):
                        for year in future_years:
                            for attr in future_attributes:
                                future_value = future_feature[f"{attr}_{year}"]
                                if future_value:
                                    # 4等分する
                                    adjusted_value = (
                                        float(future_value) / split_value
                                    )
                                    # 最後のフィーチャには余りを加える
                                    if i == len(matching_features) - 1:
                                        adjusted_value += (
                                            float(future_value) % split_value
                                        )

                                    # 見つかったフィーチャに割り当てる
                                    attribute_changes.setdefault(
                                        match_feature.id(), {}
                                    ).update(
                                        {
                                            layer.fields().indexFromName(
                                                f"future_{year}_{attr}"
                                            ): adjusted_value
                                        }
                                    )

                    processed_count += 1
                    matched_count += 1
                    if processed_count % 1000 == 0:
                        QgsMessageLog.logMessage(
                            self.tr("Processed %1/%2 features (matched: %3, skipped: %4)...").replace("%1", str(processed_count)).replace("%2", str(total_features)).replace("%3", str(matched_count)).replace("%4", str(skipped_count)),
                            self.tr("Plugin"),
                            Qgis.Info,
                        )
                        QApplication.processEvents()
                    continue

                # 部分的に人口データがある場合、既知のデータで按分し、残りは均等に分割
                for year in future_years:
                    for attr in future_attributes:
                        future_value = future_feature[f"{attr}_{year}"]
                        if (
                            future_value and future_value >= 0
                        ):  # future_value が負でないか確認
                            remaining_value = float(future_value)
                            adjusted_values = []

                            # 既知のデータで按分
                            for feature_id, population in known_population_map:
                                ratio = population / total_population
                                adjusted_value = round(
                                    float(future_value) * ratio, 6
                                )  # 丸め処理を追加
                                adjusted_values.append(adjusted_value)
                                attribute_changes.setdefault(
                                    feature_id, {}
                                ).update(
                                    {
                                        layer.fields().indexFromName(
                                            f"future_{year}_{attr}"
                                        ): adjusted_value
                                    }
                                )
                                remaining_value -= adjusted_value
                                if (
                                    remaining_value < 0
                                ):  # remaining_value が負の場合の処理
                                    remaining_value = 0

                            # 不明なフィーチャに0を割り当てる
                            for unknown_feature in unknown_features:
                                attribute_changes.setdefault(
                                    unknown_feature.id(), {}
                                ).update(
                                    {
                                        layer.fields().indexFromName(
                                            f"future_{year}_{attr}"
                                        ): 0
                                    }
                                )

                processed_count += 1
                matched_count += 1
                if processed_count % 1000 == 0:
                    QgsMessageLog.logMessage(
                        self.tr("Processed %1/%2 features (matched: %3, skipped: %4)...").replace("%1", str(processed_count)).replace("%2", str(total_features)).replace("%3", str(matched_count)).replace("%4", str(skipped_count)),
                        self.tr("Plugin"),
                        Qgis.Info,
                    )
                    QApplication.processEvents()

            # 処理結果のサマリーをログ出力
            QgsMessageLog.logMessage(
                self.tr("Processing completed: Total=%1, Matched=%2, Skipped=%3").replace("%1", str(total_features)).replace("%2", str(matched_count)).replace("%3", str(skipped_count)),
                self.tr("Plugin"),
                Qgis.Info,
            )

            # 一括でコミット
            QgsMessageLog.logMessage(
                self.tr("Applying changes to layer..."),
                self.tr("Plugin"),
                Qgis.Info,
            )

            if attribute_changes:
                layer.startEditing()
                provider.changeAttributeValues(attribute_changes)
                layer.commitChanges()

                msg = self.tr(
                    "Adding future estimated population data has been completed. Updated %1 features."
                ).replace("%1", str(len(attribute_changes)))
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Info,
                )
            else:
                QgsMessageLog.logMessage(
                    self.tr("Warning: No features were updated with future population data"),
                    self.tr("Plugin"),
                    Qgis.Warning,
                )
            QApplication.processEvents()

        except Exception as e:
            QgsMessageLog.logMessage(
                self.tr("An error occurred: %1").replace("%1", str(e)),
                self.tr("Plugin"),
                Qgis.Critical,
            )
            QApplication.processEvents()  # エラーメッセージをログに出力した後にイベントを処理

    def calculate_population_metrics(self, layer):
        """追加属性（人口増減、人口増減の変化、将来の人口増減）の計算・付与"""
        try:
            provider = layer.dataProvider()
            layer.startEditing()

            # 属性名を取得
            fields = layer.fields()

            # 年度情報を取得
            years = set()
            pattern = re.compile(r'^(\d{4})_')

            for field in fields:
                match = pattern.match(field.name())
                if match:
                    years.add(match.group(1))

            unique_years = sorted(list(years))

            # 新しいフィールドを追加
            for year in unique_years[1:]:  # 最古の年度は除外
                provider.addAttributes(
                    [
                        QgsField(f"population_diff_{year}", QVariant.Int),
                        QgsField(
                            f"population_diff_rate_{year}", QVariant.Double
                        ),
                    ]
                )
            # 最新年度のみ population_diff_rate_change フィールドを追加
            latest_year = unique_years[-1]
            provider.addAttributes(
                [
                    QgsField(
                        f"population_diff_rate_change_{latest_year}",
                        QVariant.Double,
                    )
                ]
            )

            # future_xxx_PT0 フィールド名を正規表現で取得
            future_field_pattern = re.compile(r"future_\d{4}_PT0")
            future_field_name = None
            for field in layer.fields():
                if future_field_pattern.match(field.name()):
                    future_field_name = field.name()
                    break

            if not future_field_name:
                raise Exception(
                    "将来人口フィールドが見つかりません: future_xxx_PT0"
                )

            # 将来人口差分フィールドを追加
            provider.addAttributes(
                [QgsField("population_diff_future", QVariant.Int)]
            )
            layer.updateFields()

            # 各フィーチャの計算
            for feature in layer.getFeatures():
                attributes = {}

                # 年度間の人口増減、増減率の計算
                previous_diff_rate = None
                for i in range(1, len(unique_years)):
                    current_year = unique_years[i]
                    previous_year = unique_years[i - 1]
                    current_population = feature[f"{current_year}_population"]
                    previous_population = feature[f"{previous_year}_population"]

                    if isinstance(
                        current_population, (int, float)
                    ) and isinstance(previous_population, (int, float)):
                        # 人口増減
                        population_diff = (
                            current_population - previous_population
                        )
                        attributes[f"population_diff_{current_year}"] = (
                            population_diff
                        )

                        # 人口増減率
                        if previous_population > 0:
                            population_diff_rate = (
                                population_diff / previous_population
                            ) * 100
                            attributes[
                                f"population_diff_rate_{current_year}"
                            ] = round(population_diff_rate, 2)

                            # 最新年度の人口増減率の変化
                            if current_year == latest_year:
                                if previous_diff_rate is not None:
                                    rate_change = (
                                        population_diff_rate
                                        - previous_diff_rate
                                    )
                                    attributes[
                                        f"population_diff_rate_change_{latest_year}"
                                    ] = round(rate_change, 2)
                                else:
                                    attributes[
                                        f"population_diff_rate_change_{latest_year}"
                                    ] = None

                            previous_diff_rate = population_diff_rate
                        else:
                            attributes[
                                f"population_diff_rate_{current_year}"
                            ] = None
                            if current_year == latest_year:
                                attributes[
                                    f"population_diff_rate_change_{latest_year}"
                                ] = None

                # 将来人口との差分
                future_population = feature[future_field_name]
                latest_population = feature[f"{latest_year}_population"]
                if isinstance(future_population, (int, float)) and isinstance(
                    latest_population, (int, float)
                ):
                    future_diff = future_population - latest_population
                    attributes["population_diff_future"] = future_diff

                # 属性を変更
                provider.changeAttributeValues(
                    {
                        feature.id(): {
                            layer.fields().indexOf(key): value
                            for key, value in attributes.items()
                            if key in layer.fields().names()
                        }
                    }
                )

            layer.commitChanges()

        except Exception as e:
            layer.rollBack()
            QgsMessageLog.logMessage(
                self.tr("An error occurred: %1").replace("%1", str(e)),
                self.tr("Plugin"),
                Qgis.Critical,
            )

    def __extract(self, target_layer, buffer_layer):
        """バッファレイヤ内に存在するフィーチャを抽出"""
        # 空間インデックスの作成
        processing.run("native:createspatialindex", {'INPUT': target_layer})
        processing.run("native:createspatialindex", {'INPUT': buffer_layer})

        # バッファ内のフィーチャを抽出
        result = processing.run(
            "native:extractbylocation",
            {
                'INPUT': target_layer,
                'PREDICATE': [0],  # intersect
                'INTERSECT': buffer_layer,
                'OUTPUT': 'TEMPORARY_OUTPUT',
            },
        )['OUTPUT']

        return result

    def __detect_encoding(self, file_path):
        """エンコード検出"""
        encodings = ['shift_jis', 'cp932', 'utf-8', 'utf-16']
        for encoding in encodings:
            try:
                with open(file_path, 'r', encoding=encoding) as f:
                    f.read()
                    return encoding
            except UnicodeDecodeError:
                continue

    def _extract_year_from_path(self, shp_path, base_folder):
        """
        SHPファイルのパスから年度情報を抽出する
        base_folder（27_人口集中地区）とSHPファイルの間にある「YYYY年」形式のフォルダ名から年度を取得
        """
        # SHPファイルのディレクトリパスを取得
        shp_dir = os.path.dirname(shp_path)

        # base_folderからの相対パス部分を取得
        if shp_dir.startswith(base_folder):
            relative_path = shp_dir[len(base_folder):]
            # パス区切り文字で分割
            path_parts = relative_path.split(os.sep)

            # 「YYYY年」形式のフォルダ名を検索
            year_pattern = re.compile(r'^(\d{4})年$')
            for part in path_parts:
                match = year_pattern.match(part)
                if match:
                    return match.group(1)  # YYYY部分を返す

        return None  # 年度フォルダが見つからない場合

    def load_did_data(self):
        """DID（人口集中地区）データ読み込み"""
        try:
            # base_path配下の「27_人口集中地区」フォルダを再帰的に探索してShapefileを収集
            did_folder = os.path.join(self.base_path, "27_人口集中地区")
            shp_files = self._get_shapefiles(did_folder)

            if not shp_files:
                msg = self.tr("人口集中地区のShapefileが見つかりません。スキップします。")
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Warning,
                )
                return True  # 人口集中地区データがなくても処理は続行

            # レイヤリストを作成
            layers = []
            for shp_file in shp_files:
                if self.check_canceled():
                    return  # キャンセルチェック
                # Shapefileロード（EPSG:6668で読み込み）
                layer = QgsVectorLayer(
                    shp_file, os.path.basename(shp_file), "ogr"
                )
                if not layer.isValid():
                    raise Exception(
                        f"Shapefileレイヤの読み込みに失敗しました: {shp_file}"
                    )

                # CRSをEPSG:6668（JGD2011地理座標系）に設定
                from qgis.core import QgsCoordinateReferenceSystem
                layer.setCrs(QgsCoordinateReferenceSystem("EPSG:6668"))

                # パスから年度情報を抽出
                year_value = self._extract_year_from_path(shp_file, did_folder)

                # year属性を追加するためにメモリレイヤにコピー
                # 元のレイヤの属性を取得
                original_fields = layer.fields()

                # 新しいフィールドリストを作成（year属性を追加）
                new_fields = QgsField("year", QVariant.Int)

                # メモリレイヤを作成
                geom_type = layer.geometryType()
                geom_type_str = {0: "Point", 1: "LineString", 2: "Polygon", 3: "MultiPoint", 4: "MultiLineString", 5: "MultiPolygon"}
                # MultiPolygonかPolygonかを判定
                wkb_type = layer.wkbType()
                if wkb_type in (6, 3006):  # MultiPolygon, MultiPolygonZ
                    geom_str = "MultiPolygon"
                elif wkb_type in (3, 1003):  # Polygon, PolygonZ
                    geom_str = "Polygon"
                else:
                    geom_str = geom_type_str.get(geom_type, "Polygon")

                crs_str = layer.crs().authid()
                memory_layer = QgsVectorLayer(
                    f"{geom_str}?crs={crs_str}",
                    os.path.basename(shp_file),
                    "memory"
                )
                provider = memory_layer.dataProvider()

                # 元のフィールドを追加
                provider.addAttributes(original_fields.toList())
                # year属性を追加
                provider.addAttributes([new_fields])
                memory_layer.updateFields()

                # フィーチャをコピーしてyear属性を設定
                new_features = []
                for feature in layer.getFeatures():
                    new_feature = QgsFeature(memory_layer.fields())
                    new_feature.setGeometry(feature.geometry())
                    # 元の属性をコピー
                    for field in original_fields:
                        new_feature.setAttribute(field.name(), feature[field.name()])
                    # year属性を設定（年度がなければNULL）
                    new_feature.setAttribute("year", int(year_value) if year_value else None)
                    new_features.append(new_feature)

                provider.addFeatures(new_features)

                # レイヤをリストに追加
                layers.append(memory_layer)

                # ログ出力
                year_info = year_value if year_value else "なし"
                QgsMessageLog.logMessage(
                    f"人口集中地区SHP読み込み: {shp_file}, 年度: {year_info}",
                    self.tr("Plugin"),
                    Qgis.Info,
                )

            # レイヤをマージ
            merged_layer = self.merge_layers(layers)

            # マージしたレイヤをGeoPackageに保存
            if not self.gpkg_manager.add_layer(
                merged_layer, "did", "人口集中地区"
            ):
                raise Exception(self.tr("Failed to add DID layer to GeoPackage."))

            data_name = self.tr("人口集中地区")
            msg = self.tr(
                "%1 data generation completed."
            ).replace("%1", data_name)
            QgsMessageLog.logMessage(
                msg,
                self.tr("Plugin"),
                Qgis.Info,
            )
            return merged_layer

        except Exception as e:
            # エラーメッセージをログに記録
            QgsMessageLog.logMessage(
                self.tr("An error occurred: %1").replace("%1", str(e)),
                self.tr("Plugin"),
                Qgis.Critical,
            )
            return False
