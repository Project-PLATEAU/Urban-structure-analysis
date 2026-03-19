"""
/***************************************************************************
 *
 * 【FN011】都市機能誘導関連評価指標算出機能
 *
 ***************************************************************************/
"""

import csv
from qgis.core import (
    QgsMessageLog,
    Qgis,
    QgsExpressionContext,
    QgsExpressionContextUtils,
    QgsVectorLayer,
)
from PyQt5.QtCore import QCoreApplication
import processing
from .gpkg_manager import GpkgManager


class UrbanFunctionInductionMetricCalculator:
    """都市機能誘導関連評価指標算出機能"""
    def __init__(self, base_path, check_canceled_callback=None, gpkg_manager=None, file_suffix=""):
        self.base_path = base_path

        self.check_canceled = check_canceled_callback

        self.gpkg_manager = gpkg_manager
        self.file_suffix = file_suffix

    def tr(self, message):
        """翻訳用のメソッド"""
        return QCoreApplication.translate(self.__class__.__name__, message)

    def calc(self):
        """算出処理"""
        try:
            # 建物
            buildings_layer = self.gpkg_manager.load_layer(
                'buildings', None, withload_project=False
            )
            # 誘導区域
            induction_layer = self.gpkg_manager.load_layer(
                'induction_areas', None, withload_project=False
            )
            # 仮想居住誘導区域
            hypothetical_residential_layer = self.gpkg_manager.load_layer(
                'hypothetical_residential_areas', None, withload_project=False
            )
            # 施設
            facilities_layer = self.gpkg_manager.load_layer(
                'facilities', None, withload_project=False
            )
            # 行政区域（ゾーン）
            zones_layer = self.gpkg_manager.load_layer(
                'zones', None, withload_project=False
            )

            if not buildings_layer:
                raise Exception(self.tr("The %1 layer was not found.")
                    .replace("%1", "buildings"))

            if not induction_layer:
                raise Exception(self.tr("The %1 layer was not found.")
                    .replace("%1", "induction_areas"))

            if not facilities_layer:
                raise Exception(self.tr("The %1 layer was not found.")
                    .replace("%1", "facilities"))

            if not zones_layer:
                raise Exception(self.tr("The %1 layer was not found.")
                    .replace("%1", "zones"))

            # 建物の重心を計算
            centroids_result = processing.run(
                "native:centroids",
                {
                    'INPUT': buildings_layer,
                    'ALL_PARTS': False,
                    'OUTPUT': 'memory:'
                }
            )
            centroid_layer = centroids_result['OUTPUT']

            # 空間インデックス作成
            processing.run(
                "native:createspatialindex", {'INPUT': centroid_layer}
            )

            centroid_layer = self.gpkg_manager.add_layer(
                centroid_layer, "tmp_building_centroids", None, False
            )
            if not centroid_layer:
                raise Exception(self.tr("Failed to add layer to GeoPackage."))

            # 施設レイヤから年度情報を取得（"設定年", "最新年"）
            years = set()
            for feature in facilities_layer.getFeatures():
                if feature["year"] is not None:
                    years.add(feature["year"])

            # 年度をリスト化（設定年、最新年の順）
            target_years = ["設定年", "最新年"]
            unique_years = [y for y in target_years if y in years]

            # データリストを作成
            data_list = []

            # 都市機能誘導区域（type_id=32）を取得
            urban_area_layer = QgsVectorLayer(
                "Polygon?crs=" + induction_layer.crs().authid(),
                "urban_area",
                "memory",
            )
            urban_area_data = urban_area_layer.dataProvider()
            urban_area_data.addAttributes(induction_layer.fields())
            urban_area_layer.updateFields()

            urban_area_features = []
            has_urban_area = False
            for induction_feature in induction_layer.getFeatures():
                if induction_feature["type_id"] == 32:
                    urban_area_features.append(induction_feature)
                    has_urban_area = True

            # 新しい一時レイヤに追加
            if urban_area_features:
                urban_area_data.addFeatures(urban_area_features)
            urban_area_layer.updateExtents()

            # 都市機能誘導区域がない場合はログ出力
            if not has_urban_area:
                msg = self.tr("No urban function induction area (type_id=32) found. Urban function area metrics will be empty.")
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Info,
                )

            # 空間インデックス作成
            processing.run(
                "native:createspatialindex", {'INPUT': urban_area_layer}
            )
            processing.run(
                "native:createspatialindex", {'INPUT': zones_layer}
            )

            # is_target=1のゾーンのみを抽出
            target_zones_layer = None
            if zones_layer:
                # is_target=1のフィーチャをフィルタ
                target_features = [f for f in zones_layer.getFeatures() if f['is_target'] == 1]

                if target_features:
                    # target_zones用のメモリレイヤを作成
                    target_zones_layer = QgsVectorLayer(
                        f"Polygon?crs={zones_layer.crs().authid()}",
                        "target_zones",
                        "memory"
                    )
                    target_zones_data = target_zones_layer.dataProvider()
                    target_zones_data.addAttributes(zones_layer.fields())
                    target_zones_layer.updateFields()
                    target_zones_data.addFeatures(target_features)
                    target_zones_layer.updateExtents()

                    msg = self.tr("Using %1 target zones (is_target=1) for calculation.").replace(
                        "%1", str(len(target_features))
                    )
                    QgsMessageLog.logMessage(
                        msg,
                        self.tr("Plugin"),
                        Qgis.Info,
                    )
                else:
                    # is_target=1のゾーンがない場合は集計対象なし
                    msg = self.tr("No target zones (is_target=1) found. No calculation will be performed.")
                    QgsMessageLog.logMessage(
                        msg,
                        self.tr("Plugin"),
                        Qgis.Warning,
                    )
                    target_zones_layer = None

            # target_zones_layerの空間インデックス作成
            if target_zones_layer:
                processing.run(
                    "native:createspatialindex",
                    {'INPUT': target_zones_layer}
                )

            # target_zones_layerがない場合は処理をスキップ
            if not target_zones_layer:
                msg = self.tr("No target zones available. Returning empty results.")
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Info,
                )
                self.__export_data([])
                return

            # 行政区域内の都市機能誘導区域を抽出
            admin_urban_area_result = processing.run(
                "native:extractbylocation",
                {
                    'INPUT': urban_area_layer,
                    'PREDICATE': [0],  # intersect
                    'INTERSECT': target_zones_layer,
                    'OUTPUT': 'TEMPORARY_OUTPUT',
                },
            )
            admin_urban_area_layer = admin_urban_area_result['OUTPUT']

            # 空間インデックス作成
            processing.run(
                "native:createspatialindex",
                {'INPUT': admin_urban_area_layer}
            )

            # 居住誘導区域（type_id=31）を取得、なければ仮想居住誘導区域を使用
            # まずtype_id=31の居住誘導区域を探す
            has_residential_area = False
            use_hypothetical_areas = False
            residential_area_features = []

            for induction_feature in induction_layer.getFeatures():
                if induction_feature["type_id"] == 31:
                    residential_area_features.append(induction_feature)
                    has_residential_area = True

            # 居住誘導区域がある場合は新しいレイヤを作成
            if has_residential_area:
                residential_area_layer = QgsVectorLayer(
                    "Polygon?crs=" + induction_layer.crs().authid(),
                    "residential_area",
                    "memory",
                )
                residential_area_data = residential_area_layer.dataProvider()

                # 誘導区域レイヤのフィールドを追加
                residential_area_data.addAttributes(induction_layer.fields())
                residential_area_layer.updateFields()

                # フィーチャを追加
                residential_area_data.addFeatures(residential_area_features)
                residential_area_layer.updateExtents()
            # 居住誘導区域がない場合は仮想居住誘導区域をそのまま使用
            elif hypothetical_residential_layer:
                msg = self.tr("No residential induction area (type_id=31) found. Using hypothetical residential areas.")
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Info,
                )
                residential_area_layer = hypothetical_residential_layer
                use_hypothetical_areas = True
            # どちらもない場合は空のレイヤを作成
            else:
                msg = self.tr("No residential induction area (type_id=31) or hypothetical residential areas found. Residential area metrics will be empty.")
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Warning,
                )
                residential_area_layer = QgsVectorLayer(
                    "Polygon?crs=" + induction_layer.crs().authid(),
                    "residential_area",
                    "memory",
                )
                residential_area_layer.updateExtents()

            # 空間インデックス作成
            processing.run(
                "native:createspatialindex", {'INPUT': residential_area_layer}
            )

            # 行政区域内の居住誘導区域を抽出
            admin_residential_area_result = processing.run(
                "native:extractbylocation",
                {
                    'INPUT': residential_area_layer,
                    'PREDICATE': [0],  # intersect
                    'INTERSECT': zones_layer,
                    'OUTPUT': 'TEMPORARY_OUTPUT',
                },
            )
            admin_residential_area_layer = admin_residential_area_result['OUTPUT']

            # 空間インデックス作成
            processing.run(
                "native:createspatialindex",
                {'INPUT': admin_residential_area_layer}
            )

            # 都市機能誘導区域内の建物を取得
            result = processing.run(
                "native:joinattributesbylocation",
                {
                    'INPUT': centroid_layer,
                    'JOIN': admin_urban_area_layer,
                    'PREDICATE': [5],  # overlap
                    'JOIN_FIELDS': [],
                    'METHOD': 0,
                    'OUTPUT': 'TEMPORARY_OUTPUT',
                    'DISCARD_NONMATCHING': True,
                    'PREFIX': 'induction_area_',
                },
            )

            # 結合結果の取得
            urban_buildings = result['OUTPUT']

            buildings_context = QgsExpressionContext()
            buildings_context.appendScopes(
                QgsExpressionContextUtils.globalProjectLayerScopes(
                    buildings_layer
                )
            )

            urban_context = QgsExpressionContext()
            urban_context.appendScopes(
                QgsExpressionContextUtils.globalProjectLayerScopes(
                    urban_buildings
                )
            )

            # 行政区域内の施設を抽出
            processing.run(
                "native:createspatialindex", {'INPUT': facilities_layer}
            )

            admin_facilities_result = processing.run(
                "native:extractbylocation",
                {
                    'INPUT': facilities_layer,
                    'PREDICATE': [0],  # intersect
                    'INTERSECT': target_zones_layer,
                    'OUTPUT': 'TEMPORARY_OUTPUT',
                },
            )
            admin_facilities_layer = admin_facilities_result['OUTPUT']

            # 空間インデックス作成(行政区域内施設)
            processing.run(
                "native:createspatialindex", {'INPUT': admin_facilities_layer}
            )

            # 都市機能誘導区域内の施設を取得
            result = processing.run(
                "native:joinattributesbylocation",
                {
                    'INPUT': admin_facilities_layer,
                    'JOIN': admin_urban_area_layer,
                    'PREDICATE': [5],  # overlap
                    'JOIN_FIELDS': [],
                    'METHOD': 0,
                    'OUTPUT': 'TEMPORARY_OUTPUT',
                    'DISCARD_NONMATCHING': True,
                    'PREFIX': 'induction_area_',
                },
            )

            # 結合結果の取得
            urban_facilities = result['OUTPUT']

            # 居住誘導区域内の施設を取得
            result_residential = processing.run(
                "native:joinattributesbylocation",
                {
                    'INPUT': admin_facilities_layer,
                    'JOIN': admin_residential_area_layer,
                    'PREDICATE': [5],  # overlap
                    'JOIN_FIELDS': [],
                    'METHOD': 0,
                    'OUTPUT': 'TEMPORARY_OUTPUT',
                    'DISCARD_NONMATCHING': True,
                    'PREFIX': 'residential_area_',
                },
            )

            # 結合結果の取得
            residential_facilities = result_residential['OUTPUT']

            for year in unique_years:
                if self.check_canceled():
                    return  # キャンセルチェック

                # 各施設種別の市内および都市機能誘導区域内の立地数を集計
                facility_types = [0, 1, 2, 3, 4, 5, 6, 7]  # type属性の定義

                total_qty_facilities = {}
                qty_facilities_in_urban_area = {}
                qty_facilities_in_residential_area = {}

                # 最新年データの収集（設定年・最新年の計算用）
                latest_total_qty_facilities = {}
                latest_qty_facilities_in_urban_area = {}
                latest_qty_facilities_in_residential_area = {}

                for facility_type in facility_types:
                    # 行政区域内の各施設種別の立地数をフィルタリングして集計（現在の年度）
                    expression = (
                        f'"type" = {facility_type} AND "year" = \'{year}\''
                    )
                    admin_facilities_layer.selectByExpression(
                        expression, QgsVectorLayer.SetSelection
                    )
                    total_qty_facility = admin_facilities_layer.selectedFeatureCount()
                    total_qty_facilities[facility_type] = total_qty_facility

                    # 都市機能誘導区域内の各施設種別の立地数をフィルタリングして集計（現在の年度）
                    urban_expression = (
                        f'"type" = {facility_type} AND "year" = \'{year}\''
                    )
                    urban_facilities.selectByExpression(
                        urban_expression, QgsVectorLayer.SetSelection
                    )
                    qty_facility_in_urban_area = urban_facilities.selectedFeatureCount()
                    qty_facilities_in_urban_area[facility_type] = qty_facility_in_urban_area

                    # 居住誘導区域内の各施設種別の立地数をフィルタリングして集計（現在の年度）
                    residential_facilities.selectByExpression(
                        urban_expression, QgsVectorLayer.SetSelection
                    )
                    qty_facility_in_residential_area = residential_facilities.selectedFeatureCount()
                    qty_facilities_in_residential_area[facility_type] = qty_facility_in_residential_area

                    # 最新年のデータも同時に取得
                    if "最新年" in unique_years:
                        # 最新年の行政区域内施設数
                        latest_expression = (
                            f'"type" = {facility_type} AND "year" = \'最新年\''
                        )
                        admin_facilities_layer.selectByExpression(
                            latest_expression, QgsVectorLayer.SetSelection
                        )
                        latest_total_qty_facilities[facility_type] = admin_facilities_layer.selectedFeatureCount()

                        # 最新年の都市機能誘導区域内施設数
                        urban_facilities.selectByExpression(
                            latest_expression, QgsVectorLayer.SetSelection
                        )
                        latest_qty_facilities_in_urban_area[facility_type] = urban_facilities.selectedFeatureCount()

                        # 最新年の居住誘導区域内施設数
                        residential_facilities.selectByExpression(
                            latest_expression, QgsVectorLayer.SetSelection
                        )
                        latest_qty_facilities_in_residential_area[facility_type] = residential_facilities.selectedFeatureCount()

                # type=0（都市機能誘導施設）の設定年・最新年の計算
                # 設定年のデータ（yearが"設定年"の場合は現在のyearを使用、それ以外は"設定年"から取得）
                if year == "設定年":
                    type0_admin_count_established = total_qty_facilities.get(0, 0)
                    type0_urban_count_established = qty_facilities_in_urban_area.get(0, 0)
                else:
                    # yearが"最新年"の場合、設定年のデータを別途取得
                    established_expression = f'"type" = 0 AND "year" = \'設定年\''
                    admin_facilities_layer.selectByExpression(established_expression, QgsVectorLayer.SetSelection)
                    type0_admin_count_established = admin_facilities_layer.selectedFeatureCount()

                    urban_facilities.selectByExpression(established_expression, QgsVectorLayer.SetSelection)
                    type0_urban_count_established = urban_facilities.selectedFeatureCount()

                type0_share_established = self.round_or_na(type0_urban_count_established / type0_admin_count_established, 3) if type0_admin_count_established > 0 else 0

                # 最新年のデータ（yearが"最新年"の場合は現在のyearを使用、それ以外は"最新年"から取得）
                if year == "最新年":
                    type0_admin_count_latest = total_qty_facilities.get(0, 0)
                    type0_urban_count_latest = qty_facilities_in_urban_area.get(0, 0)
                else:
                    # yearが"設定年"の場合、最新年のデータを別途取得
                    if "最新年" in unique_years:
                        latest_expression = f'"type" = 0 AND "year" = \'最新年\''
                        admin_facilities_layer.selectByExpression(latest_expression, QgsVectorLayer.SetSelection)
                        type0_admin_count_latest = admin_facilities_layer.selectedFeatureCount()

                        urban_facilities.selectByExpression(latest_expression, QgsVectorLayer.SetSelection)
                        type0_urban_count_latest = urban_facilities.selectedFeatureCount()
                    else:
                        type0_admin_count_latest = 0
                        type0_urban_count_latest = 0

                type0_share_latest = self.round_or_na(type0_urban_count_latest / type0_admin_count_latest, 3) if type0_admin_count_latest > 0 else 0

                # type=1~7の一定の都市機能の計算
                type1to7_types = [1, 2, 3, 4, 5, 6, 7]
                type1to7_admin_total = sum(total_qty_facilities.get(t, 0) for t in type1to7_types)
                type1to7_urban_total = sum(qty_facilities_in_urban_area.get(t, 0) for t in type1to7_types)
                type1to7_residential_total = sum(qty_facilities_in_residential_area.get(t, 0) for t in type1to7_types)
                type1to7_share_total = self.round_or_na(type1to7_urban_total / type1to7_admin_total, 3) if type1to7_admin_total > 0 else 0
                type1to7_residential_share_total = self.round_or_na(type1to7_residential_total / type1to7_admin_total, 3) if type1to7_admin_total > 0 else 0

                # 施設種別の組み合わせ計算（type=1~7）
                facility_categories = {
                    'admin_culture': [1, 7],  # 行政＋文化交流
                    'education_childcare': [6, 4],  # 教育＋子育て
                    'care_medical': [5, 3],  # 介護福祉＋医療
                    'commercial': [2]  # 商業
                }

                # 設定年の施設種別組み合わせ計算
                category_totals = {}
                category_urban_totals = {}
                category_shares = {}
                category_residential_totals = {}
                category_residential_shares = {}

                for category_name, types in facility_categories.items():
                    admin_count = sum(total_qty_facilities.get(t, 0) for t in types)
                    urban_count = sum(qty_facilities_in_urban_area.get(t, 0) for t in types)
                    residential_count = sum(qty_facilities_in_residential_area.get(t, 0) for t in types)

                    urban_share = self.round_or_na(urban_count / admin_count, 3) if admin_count > 0 else 0
                    residential_share = self.round_or_na(residential_count / admin_count, 3) if admin_count > 0 else 0

                    category_totals[category_name] = admin_count
                    category_urban_totals[category_name] = urban_count
                    category_shares[category_name] = urban_share
                    category_residential_totals[category_name] = residential_count
                    category_residential_shares[category_name] = residential_share


                # 前年度との変化を計算
                ufia_facility_share_delta_total = '―'
                ufia_facility_share_delta_admin_culture = '―'
                ufia_facility_share_delta_education_childcare = '―'
                ufia_facility_share_delta_care_medical = '―'
                ufia_facility_share_delta_commercial = '―'

                rpa_facility_share_delta_total = '―'
                rpa_facility_share_delta_admin_culture = '―'
                rpa_facility_share_delta_education_childcare = '―'
                rpa_facility_share_delta_care_medical = '―'
                rpa_facility_share_delta_commercial = '―'

                if data_list:
                    previous_data = data_list[-1]

                    # 都市機能誘導区域の変化
                    prev_share = previous_data.get('ufia_facility_share_total', 0)
                    if isinstance(prev_share, (int, float)):
                        ufia_facility_share_delta_total = self.round_or_na(type1to7_share_total - prev_share, 2)

                    # 居住誘導区域の変化
                    prev_residential_share = previous_data.get('rpa_facility_share_total', 0)
                    if isinstance(prev_residential_share, (int, float)):
                        rpa_facility_share_delta_total = self.round_or_na(type1to7_residential_share_total - prev_residential_share, 2)

                    for category_name in facility_categories.keys():
                        # 都市機能誘導区域の施設種別変化
                        prev_category_share = previous_data.get(f'ufia_facility_share_{category_name}', 0)
                        if isinstance(prev_category_share, (int, float)):
                            current_share = category_shares[category_name]
                            if category_name == 'admin_culture':
                                ufia_facility_share_delta_admin_culture = self.round_or_na(current_share - prev_category_share, 2)
                            elif category_name == 'education_childcare':
                                ufia_facility_share_delta_education_childcare = self.round_or_na(current_share - prev_category_share, 2)
                            elif category_name == 'care_medical':
                                ufia_facility_share_delta_care_medical = self.round_or_na(current_share - prev_category_share, 2)
                            elif category_name == 'commercial':
                                ufia_facility_share_delta_commercial = self.round_or_na(current_share - prev_category_share, 2)

                        # 居住誘導区域の施設種別変化
                        prev_residential_category_share = previous_data.get(f'rpa_facility_share_{category_name}', 0)
                        if isinstance(prev_residential_category_share, (int, float)):
                            current_residential_share = category_residential_shares[category_name]
                            if category_name == 'admin_culture':
                                rpa_facility_share_delta_admin_culture = self.round_or_na(current_residential_share - prev_residential_category_share, 2)
                            elif category_name == 'education_childcare':
                                rpa_facility_share_delta_education_childcare = self.round_or_na(current_residential_share - prev_residential_category_share, 2)
                            elif category_name == 'care_medical':
                                rpa_facility_share_delta_care_medical = self.round_or_na(current_residential_share - prev_residential_category_share, 2)
                            elif category_name == 'commercial':
                                rpa_facility_share_delta_commercial = self.round_or_na(current_residential_share - prev_residential_category_share, 2)

                # データを辞書にまとめる（新しいフォーマット）
                year_data = {
                    # 年次
                    'year': year,
                    # 都市機能誘導区域内誘導施設割合（設定年）- type=0のみ
                    'ufia_facility_share_total_established': type0_share_established,
                    # 都市機能誘導区域内施設数（設定年）- type=0のみ
                    'ufia_facility_count_total_established': type0_urban_count_established,
                    # 行政区域内施設数（設定年）- type=0のみ
                    'ufia_facility_admin_count_total_established': type0_admin_count_established,
                    # 都市機能誘導区域内誘導施設割合（最新年）- type=0のみ
                    'ufia_facility_share_total_latest': type0_share_latest,
                    # 都市機能誘導区域内施設数（最新年）- type=0のみ
                    'ufia_facility_count_total_latest': type0_urban_count_latest,
                    # 行政区域内施設数（最新年）- type=0のみ
                    'ufia_facility_admin_count_total_latest': type0_admin_count_latest,
                    # 一定の都市機能の都市機能誘導区域内割合_行政区域内施設数（全体）- type=1~7
                    'ufia_facility_admin_count_total': type1to7_admin_total,
                    # 一定の都市機能の都市機能誘導区域内割合_都市機能誘導区域内施設数（全体）- type=1~7
                    'ufia_facility_count_total': type1to7_urban_total,
                    # 一定の都市機能の都市機能誘導区域内割合_都市機能誘導区域内割合（全体）- type=1~7
                    'ufia_facility_share_total': type1to7_share_total,
                    # 一定の都市機能の都市機能誘導区域内割合_都市機能誘導区域内割合の変化（全体）
                    'ufia_facility_share_delta_total': ufia_facility_share_delta_total,
                    # 一定の都市機能の都市機能誘導区域内割合_行政区域内施設数（行政＋文化交流）
                    'ufia_facility_admin_count_admin_culture': category_totals['admin_culture'],
                    # 一定の都市機能の都市機能誘導区域内割合_都市機能誘導区域内施設数（行政＋文化交流）
                    'ufia_facility_count_admin_culture': category_urban_totals['admin_culture'],
                    # 一定の都市機能の都市機能誘導区域内割合_都市機能誘導区域内割合（行政＋文化交流）
                    'ufia_facility_share_admin_culture': category_shares['admin_culture'],
                    # 一定の都市機能の都市機能誘導区域内割合_都市機能誘導区域内割合の変化（行政＋文化交流）
                    'ufia_facility_share_delta_admin_culture': ufia_facility_share_delta_admin_culture,
                    # 一定の都市機能の都市機能誘導区域内割合_行政区域内施設数（教育＋子育て）
                    'ufia_facility_admin_count_education_childcare': category_totals['education_childcare'],
                    # 一定の都市機能の都市機能誘導区域内割合_都市機能誘導区域内施設数（教育＋子育て）
                    'ufia_facility_count_education_childcare': category_urban_totals['education_childcare'],
                    # 一定の都市機能の都市機能誘導区域内割合_都市機能誘導区域内割合（教育＋子育て）
                    'ufia_facility_share_education_childcare': category_shares['education_childcare'],
                    # 一定の都市機能の都市機能誘導区域内割合_都市機能誘導区域内割合の変化（教育＋子育て）
                    'ufia_facility_share_delta_education_childcare': ufia_facility_share_delta_education_childcare,
                    # 一定の都市機能の都市機能誘導区域内割合_行政区域内施設数（介護福祉＋医療）
                    'ufia_facility_admin_count_care_medical': category_totals['care_medical'],
                    # 一定の都市機能の都市機能誘導区域内割合_都市機能誘導区域内施設数（介護福祉＋医療）
                    'ufia_facility_count_care_medical': category_urban_totals['care_medical'],
                    # 一定の都市機能の都市機能誘導区域内割合_都市機能誘導区域内割合（介護福祉＋医療）
                    'ufia_facility_share_care_medical': category_shares['care_medical'],
                    # 一定の都市機能の都市機能誘導区域内割合_都市機能誘導区域内割合の変化（介護福祉＋医療）
                    'ufia_facility_share_delta_care_medical': ufia_facility_share_delta_care_medical,
                    # 一定の都市機能の都市機能誘導区域内割合_行政区域内施設数（商業）
                    'ufia_facility_admin_count_commercial': category_totals['commercial'],
                    # 一定の都市機能の都市機能誘導区域内割合_都市機能誘導区域内施設数（商業）
                    'ufia_facility_count_commercial': category_urban_totals['commercial'],
                    # 一定の都市機能の都市機能誘導区域内割合_都市機能誘導区域内割合（商業）
                    'ufia_facility_share_commercial': category_shares['commercial'],
                    # 一定の都市機能の都市機能誘導区域内割合_都市機能誘導区域内割合の変化（商業）
                    'ufia_facility_share_delta_commercial': ufia_facility_share_delta_commercial,
                    # 一定の都市機能の居住誘導区域内割合_行政区域内施設数（全体）
                    'rpa_facility_admin_count_total': type1to7_admin_total,
                    # 一定の都市機能の居住誘導区域内割合_居住誘導区域内施設数（全体）
                    'rpa_facility_count_total': type1to7_residential_total,
                    # 一定の都市機能の居住誘導区域内割合_居住誘導区域内割合（全体）
                    'rpa_facility_share_total': type1to7_residential_share_total,
                    # 一定の都市機能の居住誘導区域内割合_居住誘導区域内割合の変化（全体）
                    'rpa_facility_share_delta_total': rpa_facility_share_delta_total,
                    # 一定の都市機能の居住誘導区域内割合_行政区域内施設数（行政＋文化交流）
                    'rpa_facility_admin_count_admin_culture': category_totals['admin_culture'],
                    # 一定の都市機能の居住誘導区域内割合_居住誘導区域内施設数（行政＋文化交流）
                    'rpa_facility_count_admin_culture': category_residential_totals['admin_culture'],
                    # 一定の都市機能の居住誘導区域内割合_居住誘導区域内割合（行政＋文化交流）
                    'rpa_facility_share_admin_culture': category_residential_shares['admin_culture'],
                    # 一定の都市機能の居住誘導区域内割合_居住誘導区域内割合の変化（行政＋文化交流）
                    'rpa_facility_share_delta_admin_culture': rpa_facility_share_delta_admin_culture,
                    # 一定の都市機能の居住誘導区域内割合_行政区域内施設数（教育＋子育て）
                    'rpa_facility_admin_count_education_childcare': category_totals['education_childcare'],
                    # 一定の都市機能の居住誘導区域内割合_居住誘導区域内施設数（教育＋子育て）
                    'rpa_facility_count_education_childcare': category_residential_totals['education_childcare'],
                    # 一定の都市機能の居住誘導区域内割合_居住誘導区域内割合（教育＋子育て）
                    'rpa_facility_share_education_childcare': category_residential_shares['education_childcare'],
                    # 一定の都市機能の居住誘導区域内割合_居住誘導区域内割合の変化（教育＋子育て）
                    'rpa_facility_share_delta_education_childcare': rpa_facility_share_delta_education_childcare,
                    # 一定の都市機能の居住誘導区域内割合_行政区域内施設数（介護福祉＋医療）
                    'rpa_facility_admin_count_care_medical': category_totals['care_medical'],
                    # 一定の都市機能の居住誘導区域内割合_居住誘導区域内施設数（介護福祉＋医療）
                    'rpa_facility_count_care_medical': category_residential_totals['care_medical'],
                    # 一定の都市機能の居住誘導区域内割合_居住誘導区域内割合（介護福祉＋医療）
                    'rpa_facility_share_care_medical': category_residential_shares['care_medical'],
                    # 一定の都市機能の居住誘導区域内割合_居住誘導区域内割合の変化（介護福祉＋医療）
                    'rpa_facility_share_delta_care_medical': rpa_facility_share_delta_care_medical,
                    # 一定の都市機能の居住誘導区域内割合_行政区域内施設数（商業）
                    'rpa_facility_admin_count_commercial': category_totals['commercial'],
                    # 一定の都市機能の居住誘導区域内割合_居住誘導区域内施設数（商業）
                    'rpa_facility_count_commercial': category_residential_totals['commercial'],
                    # 一定の都市機能の居住誘導区域内割合_居住誘導区域内割合（商業）
                    'rpa_facility_share_commercial': category_residential_shares['commercial'],
                    # 一定の都市機能の居住誘導区域内割合_居住誘導区域内割合の変化（商業）
                    'rpa_facility_share_delta_commercial': rpa_facility_share_delta_commercial,
                    # 一定の都市機能の居住状況把握対象区域内割合_行政区域内施設数（全体）
                    'rsma_facility_admin_count_total': '',
                    # 一定の都市機能の居住状況把握対象区域内割合_居住状況把握対象区域内施設数（全体）
                    'rsma_facility_count_total': '',
                    # 一定の都市機能の居住状況把握対象区域内割合_居住状況把握対象区域内割合（全体）
                    'rsma_facility_share_total': '',
                    # 一定の都市機能の居住状況把握対象区域内割合_居住状況把握対象区域内割合の変化（全体）
                    'rsma_facility_share_delta_total': '',
                    # 一定の都市機能の居住状況把握対象区域内割合_行政区域内施設数（行政＋文化交流）
                    'rsma_facility_admin_count_admin_culture': '',
                    # 一定の都市機能の居住状況把握対象区域内割合_居住状況把握対象区域内施設数（行政＋文化交流）
                    'rsma_facility_count_admin_culture': '',
                    # 一定の都市機能の居住状況把握対象区域内割合_居住状況把握対象区域内割合（行政＋文化交流）
                    'rsma_facility_share_admin_culture': '',
                    # 一定の都市機能の居住状況把握対象区域内割合_居住状況把握対象区域内割合の変化（行政＋文化交流）
                    'rsma_facility_share_delta_admin_culture': '',
                    # 一定の都市機能の居住状況把握対象区域内割合_行政区域内施設数（教育＋子育て）
                    'rsma_facility_admin_count_education_childcare': '',
                    # 一定の都市機能の居住状況把握対象区域内割合_居住状況把握対象区域内施設数（教育＋子育て）
                    'rsma_facility_count_education_childcare': '',
                    # 一定の都市機能の居住状況把握対象区域内割合_居住状況把握対象区域内割合（教育＋子育て）
                    'rsma_facility_share_education_childcare': '',
                    # 一定の都市機能の居住状況把握対象区域内割合_居住状況把握対象区域内割合の変化（教育＋子育て）
                    'rsma_facility_share_delta_education_childcare': '',
                    # 一定の都市機能の居住状況把握対象区域内割合_行政区域内施設数（介護福祉＋医療）
                    'rsma_facility_admin_count_care_medical': '',
                    # 一定の都市機能の居住状況把握対象区域内割合_居住状況把握対象区域内施設数（介護福祉＋医療）
                    'rsma_facility_count_care_medical': '',
                    # 一定の都市機能の居住状況把握対象区域内割合_居住状況把握対象区域内割合（介護福祉＋医療）
                    'rsma_facility_share_care_medical': '',
                    # 一定の都市機能の居住状況把握対象区域内割合_居住状況把握対象区域内割合の変化（介護福祉＋医療）
                    'rsma_facility_share_delta_care_medical': '',
                    # 一定の都市機能の居住状況把握対象区域内割合_行政区域内施設数（商業）
                    'rsma_facility_admin_count_commercial': '',
                    # 一定の都市機能の居住状況把握対象区域内割合_居住状況把握対象区域内施設数（商業）
                    'rsma_facility_count_commercial': '',
                    # 一定の都市機能の居住状況把握対象区域内割合_居住状況把握対象区域内割合（商業）
                    'rsma_facility_share_commercial': '',
                    # 一定の都市機能の居住状況把握対象区域内割合_居住状況把握対象区域内割合の変化（商業）
                    'rsma_facility_share_delta_commercial': '',
                    # A面記載の居住誘導区域内割合_居住誘導区域内割合（全体）
                    'sheet_a_rpa_facility_share_total': '',
                    # A面記載の居住誘導区域内割合_居住誘導区域内割合（行政＋文化交流）
                    'sheet_a_rpa_facility_share_admin_culture': '',
                    # A面記載の居住誘導区域内割合_居住誘導区域内割合（教育＋子育て）
                    'sheet_a_rpa_facility_share_education_childcare': '',
                    # A面記載の居住誘導区域内割合_居住誘導区域内割合（介護福祉＋医療）
                    'sheet_a_rpa_facility_share_care_medical': '',
                    # A面記載の居住誘導区域内割合_居住誘導区域内割合（商業）
                    'sheet_a_rpa_facility_share_commercial': '',
                }

                # 辞書をリストに追加
                data_list.append(year_data)

            # エクスポート（空の場合はヘッダーだけのCSVを出力）
            self.__export_data(data_list)

            return

        except Exception as e:
            # エラーメッセージのログ出力
            QgsMessageLog.logMessage(
                self.tr("An error occurred: %1").replace("%1", str(e)),
                self.tr("Plugin"),
                Qgis.Critical,
            )
            raise e

    def __export_data(self, data_list):
        """データをCSVにエクスポート（空の場合はヘッダーだけのCSVを出力）"""
        if not data_list:
            data_list = [{
                'year': '',
                'ufia_facility_share_total_established': '',
                'ufia_facility_count_total_established': '',
                'ufia_facility_admin_count_total_established': '',
                'ufia_facility_share_total_latest': '',
                'ufia_facility_count_total_latest': '',
                'ufia_facility_admin_count_total_latest': '',
                'ufia_facility_admin_count_total': '',
                'ufia_facility_count_total': '',
                'ufia_facility_share_total': '',
                'ufia_facility_share_delta_total': '',
                'ufia_facility_admin_count_admin_culture': '',
                'ufia_facility_count_admin_culture': '',
                'ufia_facility_share_admin_culture': '',
                'ufia_facility_share_delta_admin_culture': '',
                'ufia_facility_admin_count_education_childcare': '',
                'ufia_facility_count_education_childcare': '',
                'ufia_facility_share_education_childcare': '',
                'ufia_facility_share_delta_education_childcare': '',
                'ufia_facility_admin_count_care_medical': '',
                'ufia_facility_count_care_medical': '',
                'ufia_facility_share_care_medical': '',
                'ufia_facility_share_delta_care_medical': '',
                'ufia_facility_admin_count_commercial': '',
                'ufia_facility_count_commercial': '',
                'ufia_facility_share_commercial': '',
                'ufia_facility_share_delta_commercial': '',
                'rpa_facility_admin_count_total': '',
                'rpa_facility_count_total': '',
                'rpa_facility_share_total': '',
                'rpa_facility_share_delta_total': '',
                'rpa_facility_admin_count_admin_culture': '',
                'rpa_facility_count_admin_culture': '',
                'rpa_facility_share_admin_culture': '',
                'rpa_facility_share_delta_admin_culture': '',
                'rpa_facility_admin_count_education_childcare': '',
                'rpa_facility_count_education_childcare': '',
                'rpa_facility_share_education_childcare': '',
                'rpa_facility_share_delta_education_childcare': '',
                'rpa_facility_admin_count_care_medical': '',
                'rpa_facility_count_care_medical': '',
                'rpa_facility_share_care_medical': '',
                'rpa_facility_share_delta_care_medical': '',
                'rpa_facility_admin_count_commercial': '',
                'rpa_facility_count_commercial': '',
                'rpa_facility_share_commercial': '',
                'rpa_facility_share_delta_commercial': '',
                'rsma_facility_admin_count_total': '',
                'rsma_facility_count_total': '',
                'rsma_facility_share_total': '',
                'rsma_facility_share_delta_total': '',
                'rsma_facility_admin_count_admin_culture': '',
                'rsma_facility_count_admin_culture': '',
                'rsma_facility_share_admin_culture': '',
                'rsma_facility_share_delta_admin_culture': '',
                'rsma_facility_admin_count_education_childcare': '',
                'rsma_facility_count_education_childcare': '',
                'rsma_facility_share_education_childcare': '',
                'rsma_facility_share_delta_education_childcare': '',
                'rsma_facility_admin_count_care_medical': '',
                'rsma_facility_count_care_medical': '',
                'rsma_facility_share_care_medical': '',
                'rsma_facility_share_delta_care_medical': '',
                'rsma_facility_admin_count_commercial': '',
                'rsma_facility_count_commercial': '',
                'rsma_facility_share_commercial': '',
                'rsma_facility_share_delta_commercial': '',
                'sheet_a_rpa_facility_share_total': '',
                'sheet_a_rpa_facility_share_admin_culture': '',
                'sheet_a_rpa_facility_share_education_childcare': '',
                'sheet_a_rpa_facility_share_care_medical': '',
                'sheet_a_rpa_facility_share_commercial': '',
            }]
        self.export(
            self.base_path
            + f'\\IF102_都市機能誘導区域関連評価指標ファイル{self.file_suffix}.csv',
            data_list,
        )

    def export(self, file_path, data):
        """エクスポート処理"""
        try:
            if not data:
                raise Exception(self.tr("The data to export is empty."))

            # データ項目からヘッダーを取得
            headers = list(data[0].keys())

            # CSVファイル書き込み
            with open(
                file_path, mode='w', newline='', encoding='utf-8'
            ) as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=headers)
                writer.writeheader()

                for row in data:
                    # 全値が空文字の行（ヘッダー定義用）はスキップ
                    if any(v != '' for v in row.values()):
                        writer.writerow(row)

            msg = self.tr(
                "File export completed: %1."
            ).replace("%1", file_path)
            QgsMessageLog.logMessage(
                msg,
                self.tr("Plugin"),
                Qgis.Info,
            )
            return True
        except Exception as e:
            # エラーメッセージのログ出力
            msg = self.tr(
                "An error occurred during file export: %1."
            ).replace("%1", str(e))
            QgsMessageLog.logMessage(
                msg,
                self.tr("Plugin"),
                Qgis.Critical,
            )
            raise e

    def round_or_na(self, value, decimal_places, threshold=None):
        """丸め処理"""
        if value is None or (threshold is not None and value <= threshold):
            return '―'
        else:
            return round(value, decimal_places)
