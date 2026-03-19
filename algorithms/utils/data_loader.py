"""
/***************************************************************************
 *
 * 【FN002】データ読み込み機能
 *
 ***************************************************************************/
"""

import re
import os
import csv
import glob
import traceback
from qgis.core import (
    QgsMessageLog,
    Qgis,
    QgsVectorLayer,
    QgsField,
    QgsFeature,
    QgsCoordinateReferenceSystem,
)
from PyQt5.QtCore import QCoreApplication, QVariant
import processing


class BuildingLayerNotFoundError(Exception):
    """bldg:Building レイヤが見つからない場合の専用例外"""
    pass


class ShapefileNotFoundError(Exception):
    """有効なShapefileが見つからない場合の専用例外"""
    pass


class DataLoader:
    """データ読み込み機能"""
    def __init__(self, check_canceled_callback=None, base_path=None, gpkg_manager=None):
        # GeoPackageマネージャー
        self.gpkg_manager = gpkg_manager

        self.check_canceled = check_canceled_callback
        self.base_path = base_path

    def tr(self, message):
        """翻訳用のメソッド"""
        return QCoreApplication.translate(self.__class__.__name__, message)

    def load_buildings(self):
        """都市モデル（建物）フォルダのGeopackageまたはShapefileから建物レイヤを生成"""
        try:
            # 01_都市モデル（建物）フォルダからGeopackageファイルを取得
            building_dir = os.path.join(self.base_path, "01_都市モデル（建物）")
            gpkg_files = glob.glob(os.path.join(building_dir, "*.gpkg"))

            if not gpkg_files:
                # GeoPackageがない場合はShapefileを探す
                return self._load_buildings_from_shapefile(building_dir)

            # 最初のGeopackageファイルを使用
            gpkg_path = gpkg_files[0]

            # Geopackageから"bldg:Building"レイヤを読み込み
            building_layer = QgsVectorLayer(
                f"{gpkg_path}|layername=bldg:Building",
                "bldg:Building",
                "ogr"
            )

            if not building_layer.isValid():
                msg = self.tr(
                    "bldg:Building layer not found in Geopackage file."
                )
                raise BuildingLayerNotFoundError(msg)

            # "uro:BuildingDetailAttribute"レイヤを取得
            building_detail_layer = QgsVectorLayer(
                f"{gpkg_path}|layername=uro:BuildingDetailAttribute",
                "uro:BuildingDetailAttribute",
                "ogr"
            )

            if not building_detail_layer.isValid():
                msg = self.tr(
                    "uro:BuildingDetailAttribute layer not found in Geopackage file."
                )
                raise BuildingLayerNotFoundError(msg)

            # bldg:BuildingレイヤにBuildingDetailレイヤをLeftJoin
            joined_layer = self.join_with_detail(
                building_layer, building_detail_layer, join_field='parentId'
            )

            # JOINの結果がメモリレイヤとして有効か確認
            if (
                not isinstance(joined_layer, QgsVectorLayer)
                or not joined_layer.isValid()
            ):
                raise Exception(self.tr("Failed to join layers."))

            # uro:RiverFloodingRiskAttributeレイヤを取得
            flooding_layer = QgsVectorLayer(
                f"{gpkg_path}|layername=uro:RiverFloodingRiskAttribute",
                "uro:RiverFloodingRiskAttribute",
                "ogr"
            )

            if flooding_layer.isValid():
                # scale属性でL1とL2を分けて浸水深を結合
                joined_layer = self.add_flooding_depth(
                    joined_layer, flooding_layer
                )
            else:
                # レイヤが存在しない場合でも空のフィールドを追加
                joined_layer.dataProvider().addAttributes([
                    QgsField("flood_depth_l1", QVariant.Double),
                    QgsField("flood_depth_l2", QVariant.Double)
                ])
                joined_layer.updateFields()
                msg = self.tr(
                    "uro:RiverFloodingRiskAttribute layer not found. Proceeding without flood risk data."
                )
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Info,
                )

            # フィールド名をスネークケースに変換
            self.convert_fields_to_snake_case(joined_layer)

            # usageフィールドの["~"]形式を除去
            self.cleanup_usage_field(joined_layer)

            # 無効なジオメトリを修正する
            joined_layer = self.__fix_invalid_geometries(joined_layer)

            # GeoPackageに保存
            if not self.gpkg_manager.add_layer(
                joined_layer, "buildings", "建築物"
            ):
                raise Exception(self.tr("Failed to add layer to GeoPackage."))

            return True

        except BuildingLayerNotFoundError as e:
            # Building層が見つからない場合
            QgsMessageLog.logMessage(
                str(e),
                self.tr("Plugin"),
                Qgis.Critical,
            )
            raise
        except ShapefileNotFoundError as e:
            # 有効なShapefileが見つからない場合
            QgsMessageLog.logMessage(
                str(e),
                self.tr("Plugin"),
                Qgis.Critical,
            )
            raise
        except Exception as e:
            # その他のエラーの場合
            # ログにはスタックトレースを含む詳細情報を出力
            error_detail = f"{str(e)}\n{traceback.format_exc()}"
            QgsMessageLog.logMessage(
                self.tr("An error occurred: %1").replace("%1", error_detail),
                self.tr("Plugin"),
                Qgis.Critical,
            )
            # ユーザーには簡潔なメッセージを表示
            raise Exception(self.tr("Failed to load data.")) from e

    def join_with_detail(
        self,
        building_layer,
        detail_layer,
        join_field='parentId',
        target_field='id',
    ):
        """bldg:BuildingレイヤにBuildingDetailレイヤをLeftJoinする"""
        # processing.runを使用してレイヤを結合する
        result = processing.run(
            "native:joinattributestable",
            {
                'INPUT': building_layer,
                'FIELD': target_field,
                'INPUT_2': detail_layer,
                'FIELD_2': join_field,
                'FIELDS_TO_COPY': detail_layer.fields().names(),
                'METHOD': 0,  # LeftJoin
                'DISCARD_NONMATCHING': False,  # 一致しないレコードも保持する
                'PREFIX': '',
                'OUTPUT': 'memory:',
            },
        )
        return result['OUTPUT']

    def convert_fields_to_snake_case(self, layer: QgsVectorLayer):
        """レイヤ内の全フィールド名をスネークケースに変換"""
        provider = layer.dataProvider()
        for field in layer.fields():
            snake_case_name = self.to_snake_case(field.name())
            provider.renameAttributes(
                {layer.fields().indexOf(field.name()): snake_case_name}
            )
        layer.updateFields()

    def to_snake_case(self, name):
        """フィールド名をスネークケースに変換"""
        name = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
        name = re.sub('([a-z0-9])([A-Z])', r'\1_\2', name)
        return name.lower()

    def add_flooding_depth(
        self, building_layer, risk_layer, join_field='parentId'
    ):
        """scale属性でL1/L2を判定し、浸水深フィールドを建物レイヤに追加"""
        # scale属性でL1とL2を分離したレイヤを作成
        merged_risk_layer_l1 = self.merge_risk_layers_by_scale(risk_layer, join_field, 'L1')
        merged_risk_layer_l2 = self.merge_risk_layers_by_scale(risk_layer, join_field, 'L2')

        # L1の浸水深を結合
        if merged_risk_layer_l1:
            result = processing.run(
                "native:joinattributestable",
                {
                    'INPUT': building_layer,
                    'FIELD': 'id',
                    'INPUT_2': merged_risk_layer_l1,
                    'FIELD_2': join_field,
                    'FIELDS_TO_COPY': ['depth'],
                    'METHOD': 0,  # Left Join
                    'DISCARD_NONMATCHING': False,
                    'PREFIX': '',
                    'OUTPUT': 'memory:',
                },
            )
            building_layer = result['OUTPUT']
            # depth列をflood_depth_l1にリネーム
            idx = building_layer.fields().indexOf('depth')
            if idx != -1:
                building_layer.dataProvider().renameAttributes({idx: 'flood_depth_l1'})
                building_layer.updateFields()
        else:
            # L1データがない場合は空のフィールドを追加
            building_layer.dataProvider().addAttributes([QgsField('flood_depth_l1', QVariant.Double)])
            building_layer.updateFields()

        # L2の浸水深を結合
        if merged_risk_layer_l2:
            result = processing.run(
                "native:joinattributestable",
                {
                    'INPUT': building_layer,
                    'FIELD': 'id',
                    'INPUT_2': merged_risk_layer_l2,
                    'FIELD_2': join_field,
                    'FIELDS_TO_COPY': ['depth'],
                    'METHOD': 0,  # Left Join
                    'DISCARD_NONMATCHING': False,
                    'PREFIX': '',
                    'OUTPUT': 'memory:',
                },
            )
            building_layer = result['OUTPUT']
            # depth列をflood_depth_l2にリネーム
            idx = building_layer.fields().indexOf('depth')
            if idx != -1:
                building_layer.dataProvider().renameAttributes({idx: 'flood_depth_l2'})
                building_layer.updateFields()
        else:
            # L2データがない場合は空のフィールドを追加
            building_layer.dataProvider().addAttributes([QgsField('flood_depth_l2', QVariant.Double)])
            building_layer.updateFields()

        return building_layer

    def merge_risk_layers_by_scale(self, risk_layer, join_field, scale_type):
        """scale属性でフィルタリングし、最大浸水深を計算して統合レイヤを作成"""
        risk_features = {}

        for feature in risk_layer.getFeatures():
            parent_id = feature[join_field]
            depth = feature['depth']
            scale = feature['scale']

            # scale属性でL1/L2を判定
            if scale_type == 'L1' and ('L1' in str(scale) or '計画規模' in str(scale)):
                if parent_id in risk_features:
                    risk_features[parent_id] = max(risk_features[parent_id], depth)
                else:
                    risk_features[parent_id] = depth
            elif scale_type == 'L2' and ('L2' in str(scale) or '想定最大規模' in str(scale)):
                if parent_id in risk_features:
                    risk_features[parent_id] = max(risk_features[parent_id], depth)
                else:
                    risk_features[parent_id] = depth

        # データがない場合はNoneを返す
        if not risk_features:
            return None

        # 統合レイヤを作成
        merged_layer = QgsVectorLayer(
            "Point?crs=EPSG:4326", f"merged_risk_layer_{scale_type}", "memory"
        )
        provider = merged_layer.dataProvider()
        provider.addAttributes(
            [
                QgsField(join_field, QVariant.String),
                QgsField('depth', QVariant.Double),
            ]
        )
        merged_layer.updateFields()

        # 統合データを追加
        new_features = []
        for parent_id, depth in risk_features.items():
            new_feature = QgsFeature()
            new_feature.setAttributes([parent_id, depth])
            new_features.append(new_feature)
        provider.addFeatures(new_features)

        return merged_layer


    def cleanup_usage_field(self, layer: QgsVectorLayer):
        """usageフィールドの["~"]形式を除去して中身の文字列だけを残す"""
        # usageフィールドのインデックスを取得
        usage_idx = layer.fields().indexOf('usage')
        if usage_idx == -1:
            return  # usageフィールドが存在しない場合は何もしない

        # レイヤの編集を開始
        layer.startEditing()

        # 各フィーチャのusageフィールドをクリーンアップ
        for feature in layer.getFeatures():
            usage_value = feature['usage']

            if usage_value and isinstance(usage_value, str):
                # ["~"]形式の場合、中身だけを取り出す
                if usage_value.startswith('["') and usage_value.endswith('"]'):
                    cleaned_value = usage_value[2:-2]  # 先頭の["と末尾の"]を除去
                    layer.changeAttributeValue(feature.id(), usage_idx, cleaned_value)

        # 変更をコミット
        layer.commitChanges()


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

    def _load_attribute_mapping(self, building_dir):
        """建物属性対応表.csvを読み込む"""
        csv_path = os.path.join(building_dir, "建物属性対応表.csv")
        if not os.path.exists(csv_path):
            return None

        attribute_mapping = {}
        with open(csv_path, mode='r', encoding='cp932') as file:
            reader = csv.DictReader(file)
            for row in reader:
                source_field = row.get('建物利用現況調査結果データ項目名称', '').strip()
                target_field = row.get('変換先項目名称', '').strip()
                if source_field and target_field:
                    attribute_mapping[source_field] = target_field
        return attribute_mapping

    def _load_usage_mapping(self, building_dir):
        """建物用途対応表.csvを読み込む"""
        csv_path = os.path.join(building_dir, "建物用途対応表.csv")
        if not os.path.exists(csv_path):
            return None

        usage_mapping = {}
        with open(csv_path, mode='r', encoding='cp932') as file:
            reader = csv.DictReader(file)
            for row in reader:
                source_value = row.get('建物用途データ値', '').strip()
                target_value = row.get('変換先項目名称', '').strip()
                if source_value and target_value:
                    usage_mapping[source_value] = target_value
        return usage_mapping

    def _load_buildings_from_shapefile(self, building_dir):
        """Shapefileから建物レイヤを読み込む"""
        # 変換対応表を読み込む
        attribute_mapping = self._load_attribute_mapping(building_dir)
        usage_mapping = self._load_usage_mapping(building_dir)

        if not attribute_mapping:
            msg = self.tr(
                "No attribute mapping defined in '建物属性対応表.csv'. "
                "Please fill in the mapping table."
            )
            raise ShapefileNotFoundError(msg)

        # 必要な属性（変換元の属性名）を取得
        required_source_fields = list(attribute_mapping.keys())

        # Shapefileを探す（サブフォルダも含む）
        shp_files = glob.glob(os.path.join(building_dir, "**", "*.shp"), recursive=True)
        if not shp_files:
            msg = self.tr(
                "'都市モデル（建物）' folder does not contain any Geopackage or Shapefile files."
            )
            raise ShapefileNotFoundError(msg)

        # 有効なShapefileを探す
        valid_layer = None
        valid_shp_path = None
        for shp_path in shp_files:
            layer = QgsVectorLayer(shp_path, "building_check", "ogr")
            if not layer.isValid():
                QgsMessageLog.logMessage(
                    self.tr("Skipping invalid shapefile: %1").replace("%1", shp_path),
                    self.tr("Plugin"),
                    Qgis.Warning,
                )
                continue

            # CRSが不明なShapefileはスキップ
            if not layer.crs().isValid():
                QgsMessageLog.logMessage(
                    self.tr("Skipping shapefile '%1' - CRS is unknown. Please add a .prj file.").replace(
                        "%1", shp_path
                    ),
                    self.tr("Plugin"),
                    Qgis.Warning,
                )
                continue

            # 必要な属性がすべて存在するか確認
            layer_fields = [field.name() for field in layer.fields()]
            missing_fields = [f for f in required_source_fields if f not in layer_fields]
            if missing_fields:
                QgsMessageLog.logMessage(
                    self.tr("Skipping shapefile '%1' - missing required fields: %2").replace(
                        "%1", shp_path
                    ).replace("%2", ", ".join(missing_fields)),
                    self.tr("Plugin"),
                    Qgis.Warning,
                )
                continue

            # 有効なShapefileが見つかった
            valid_layer = layer
            valid_shp_path = shp_path
            break

        if valid_layer is None:
            msg = self.tr(
                "No valid Shapefile found. Please ensure the shapefile has: "
                "1) a valid .prj file (CRS definition), "
                "2) required attributes defined in '建物属性対応表.csv'."
            )
            raise ShapefileNotFoundError(msg)

        QgsMessageLog.logMessage(
            self.tr("Loading building data from shapefile: %1").replace("%1", valid_shp_path),
            self.tr("Plugin"),
            Qgis.Info,
        )

        # CRSがEPSG:6677でない場合は変換
        target_crs = QgsCoordinateReferenceSystem("EPSG:6677")
        if valid_layer.crs() != target_crs:
            QgsMessageLog.logMessage(
                self.tr("Reprojecting from %1 to EPSG:6677").replace(
                    "%1", valid_layer.crs().authid()
                ),
                self.tr("Plugin"),
                Qgis.Info,
            )
            result = processing.run(
                "native:reprojectlayer",
                {
                    'INPUT': valid_layer,
                    'TARGET_CRS': target_crs,
                    'OUTPUT': 'memory:reprojected'
                }
            )
            valid_layer = result['OUTPUT']

        # メモリレイヤを作成して属性を変換
        converted_layer = self._convert_shapefile_attributes(
            valid_layer, attribute_mapping, usage_mapping
        )

        # 無効なジオメトリを修正する
        converted_layer = self.__fix_invalid_geometries(converted_layer)

        # GeoPackageに保存
        if not self.gpkg_manager.add_layer(
            converted_layer, "buildings", "建築物"
        ):
            raise Exception(self.tr("Failed to add layer to GeoPackage."))

        return True

    def _convert_shapefile_attributes(self, source_layer, attribute_mapping, usage_mapping):
        """Shapefileの属性を変換対応表に基づいて変換する"""
        # 必須フィールドの定義（PLATEAU仕様）
        # 9999は「不明」を表す
        required_fields = {
            'usage': {'type': QVariant.String, 'default': None},
            'storeysAboveGround': {'type': QVariant.Int, 'default': 9999},
            'storeysBelowGround': {'type': QVariant.Int, 'default': 9999},
            'totalFloorArea': {'type': QVariant.Double, 'default': None},
            'yearOfConstruction': {'type': QVariant.Int, 'default': None},
        }

        # マッピングされたフィールドを確認
        mapped_target_fields = set(attribute_mapping.values())

        # 変換後のフィールドを定義（マッピングされたフィールド + 未マッピングの必須フィールド）
        target_fields = []
        field_order = []  # フィールドの順序を保持

        # まずマッピングされたフィールドを追加
        for target_field in attribute_mapping.values():
            field_type = required_fields.get(target_field, {}).get('type', QVariant.String)
            target_fields.append(QgsField(target_field, field_type))
            field_order.append(target_field)

        # 未マッピングの必須フィールドを追加（デフォルト値を設定するため）
        for field_name, field_info in required_fields.items():
            if field_name not in mapped_target_fields:
                target_fields.append(QgsField(field_name, field_info['type']))
                field_order.append(field_name)

        # 浸水深フィールドを追加（Shapefileには存在しないため空で追加）
        target_fields.append(QgsField("flood_depth_l1", QVariant.Double))
        target_fields.append(QgsField("flood_depth_l2", QVariant.Double))
        field_order.append("flood_depth_l1")
        field_order.append("flood_depth_l2")

        # メモリレイヤを作成
        crs = source_layer.crs().authid()
        geom_type = source_layer.geometryType()
        geom_type_str = {0: "Point", 1: "LineString", 2: "Polygon", 3: "MultiPoint", 4: "MultiLineString", 5: "MultiPolygon"}.get(geom_type, "Polygon")

        converted_layer = QgsVectorLayer(
            f"{geom_type_str}?crs={crs}",
            "converted_buildings",
            "memory"
        )
        provider = converted_layer.dataProvider()
        provider.addAttributes(target_fields)
        converted_layer.updateFields()

        # フィーチャを変換してコピー
        new_features = []
        for feature in source_layer.getFeatures():
            new_feature = QgsFeature()
            new_feature.setGeometry(feature.geometry())

            # 属性を変換
            attributes = []

            # マッピングされたフィールドの値を取得
            for source_field, target_field in attribute_mapping.items():
                value = feature[source_field]

                # usageフィールドの場合は用途対応表で変換
                if target_field == 'usage' and usage_mapping:
                    converted_value = usage_mapping.get(str(value).strip(), value)
                    attributes.append(converted_value)
                else:
                    attributes.append(value)

            # 未マッピングの必須フィールドにはデフォルト値を設定
            for field_name, field_info in required_fields.items():
                if field_name not in mapped_target_fields:
                    attributes.append(field_info['default'])

            # 浸水深フィールドはNULL
            attributes.append(None)  # flood_depth_l1
            attributes.append(None)  # flood_depth_l2

            new_feature.setAttributes(attributes)
            new_features.append(new_feature)

        provider.addFeatures(new_features)

        # フィールド名をスネークケースに変換
        self.convert_fields_to_snake_case(converted_layer)

        return converted_layer
