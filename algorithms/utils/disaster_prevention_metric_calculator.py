"""
/***************************************************************************
 *
 * 【FN012】防災関連評価指標算出機能
 *
 ***************************************************************************/
"""

import re
import csv
from qgis.core import (
    QgsMessageLog,
    Qgis,
    QgsAggregateCalculator,
    QgsVectorLayer,
    QgsFeature,
)
from PyQt5.QtCore import QCoreApplication
import processing
from .gpkg_manager import GpkgManager


class DisasterPreventionMetricCalculator:
    """防災関連評価指標算出機能"""
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
            # 計画規模(L1)
            hazard_area_l1_layer = self.gpkg_manager.load_layer(
                'hazard_area_planned_scales', None, withload_project=False
            )
            # 想定最大規模(L2)
            hazard_area_l2_layer = self.gpkg_manager.load_layer(
                'hazard_area_maximum_scales', None, withload_project=False
            )
            # 津波
            hazard_area_tsunamis_layer = self.gpkg_manager.load_layer(
                'hazard_area_tsunamis', None, withload_project=False
            )
            # 行政区域
            zones_layer = self.gpkg_manager.load_layer(
                'zones', None, withload_project=False
            )

            if not buildings_layer:
                raise Exception(self.tr("The %1 layer was not found.")
                                .replace("%1", "buildings"))

            if not zones_layer:
                raise Exception(self.tr("The %1 layer was not found.")
                    .replace("%1", "zones"))

            # is_target=1のzonesを取得してフィルタリング用のレイヤを作成
            target_zones_layer = None
            if zones_layer:
                target_zones_layer = QgsVectorLayer(
                    "Polygon?crs=" + zones_layer.crs().authid(),
                    "target_zones",
                    "memory",
                )
                target_zones_data = target_zones_layer.dataProvider()
                target_zones_data.addAttributes(zones_layer.fields())
                target_zones_layer.updateFields()

                target_zones_features = []
                for zone_feature in zones_layer.getFeatures():
                    if zone_feature["is_target"] == 1:
                        target_zones_features.append(zone_feature)

                if target_zones_features:
                    target_zones_data.addFeatures(target_zones_features)
                    target_zones_layer.updateExtents()
                else:
                    target_zones_layer = None

            # target_zones_layerがない場合は集計を行わない
            if not target_zones_layer:
                self.__export_data([])
                return

            # 建物の重心を計算
            centroids_result = processing.run(
                "native:centroids",
                {
                    'INPUT': buildings_layer,
                    'ALL_PARTS': False,
                    'OUTPUT': 'memory:'
                }
            )
            centroids_all = centroids_result['OUTPUT']

            # 空間インデックス作成
            processing.run(
                "native:createspatialindex", {'INPUT': centroids_all}
            )
            processing.run(
                "native:createspatialindex", {'INPUT': target_zones_layer}
            )

            # target_zones内の重心のみを抽出
            if target_zones_layer and target_zones_layer.featureCount() > 0:
                joined_result = processing.run(
                    "native:joinattributesbylocation",
                    {
                        'INPUT': centroids_all,
                        'JOIN': target_zones_layer,
                        'PREDICATE': [5],  # within
                        'JOIN_FIELDS': [],  # フィールド結合は不要
                        'METHOD': 0,
                        'DISCARD_NONMATCHING': True,  # マッチしないものは除外
                        'PREFIX': '',
                        'OUTPUT': 'memory:'
                    }
                )
                centroid_layer = joined_result['OUTPUT']
            else:
                # target_zonesがない場合は全重心を使用
                centroid_layer = centroids_all

            # 空間インデックス作成
            processing.run(
                "native:createspatialindex", {'INPUT': centroid_layer}
            )

            centroid_layer = self.gpkg_manager.add_layer(
                centroid_layer, "tmp_building_centroids", None, False
            )
            if not centroid_layer:
                raise Exception(self.tr("Failed to add layer to GeoPackage."))

            # 各ハザードエリアと行政区域の交差処理
            # target_zones_layerと交差するハザードエリアのみを抽出

            # L1ハザードエリア（計画規模）の集計対象の行政区域でフィルタ抽出
            hazard_area_l1_constrained = None
            if hazard_area_l1_layer:
                processing.run(
                    "native:createspatialindex",
                    {'INPUT': hazard_area_l1_layer}
                )
                l1_constrained_result = processing.run(
                    "native:extractbylocation",
                    {
                        'INPUT': hazard_area_l1_layer,
                        'INTERSECT': target_zones_layer,
                        'PREDICATE': [0],  # intersect
                        'OUTPUT': 'TEMPORARY_OUTPUT',
                    },
                )
                hazard_area_l1_constrained = l1_constrained_result['OUTPUT']

            # L2ハザードエリア（想定最大規模）の集計対象の行政区域でフィルタ抽出
            hazard_area_l2_constrained = None
            if hazard_area_l2_layer:
                processing.run(
                    "native:createspatialindex",
                    {'INPUT': hazard_area_l2_layer}
                )
                l2_constrained_result = processing.run(
                    "native:extractbylocation",
                    {
                        'INPUT': hazard_area_l2_layer,
                        'INTERSECT': target_zones_layer,
                        'PREDICATE': [0],  # intersect
                        'OUTPUT': 'TEMPORARY_OUTPUT',
                    },
                )
                hazard_area_l2_constrained = l2_constrained_result['OUTPUT']

            # 津波ハザードエリアの集計対象の行政区域でフィルタ抽出
            hazard_area_tsunami_constrained = None
            if hazard_area_tsunamis_layer:
                processing.run(
                    "native:createspatialindex",
                    {'INPUT': hazard_area_tsunamis_layer}
                )
                tsunami_constrained_result = processing.run(
                    "native:extractbylocation",
                    {
                        'INPUT': hazard_area_tsunamis_layer,
                        'INTERSECT': target_zones_layer,
                        'PREDICATE': [0],  # intersect
                        'OUTPUT': 'TEMPORARY_OUTPUT',
                    },
                )
                hazard_area_tsunami_constrained = tsunami_constrained_result['OUTPUT']

            if self.check_canceled():
                return  # キャンセルチェック

            # 属性名を取得
            fields = buildings_layer.fields()

            # 年度情報を取得
            years = set()
            pattern = re.compile(r'^(\d{4})_')

            for field in fields:
                match = pattern.match(field.name())
                if match:
                    years.add(match.group(1))

            # 年度をリスト化してソート
            unique_years = sorted(list(years))

            # データリストを作成
            data_list = []

            # 各ハザードエリア内の建物を取得（深度別フィルタリング）
            if self.check_canceled():
                return  # キャンセルチェック

            # L1範囲の建物を取得（全レベル）
            l1_buildings = None
            if hazard_area_l1_constrained:
                processing.run(
                    "native:createspatialindex",
                    {'INPUT': hazard_area_l1_constrained}
                )
                l1_buildings_result = processing.run(
                    "native:joinattributesbylocation",
                    {
                        'INPUT': centroid_layer,
                        'JOIN': hazard_area_l1_constrained,
                        'PREDICATE': [5],  # overlap
                        'JOIN_FIELDS': ['rank'],
                        'METHOD': 0,
                        'DISCARD_NONMATCHING': True,
                        'PREFIX': 'l1_',
                        'OUTPUT': 'TEMPORARY_OUTPUT',
                    },
                )
                l1_buildings = l1_buildings_result['OUTPUT']

            if self.check_canceled():
                return  # キャンセルチェック

            # L2範囲の建物を取得（全レベル）
            l2_buildings = None
            if hazard_area_l2_constrained:
                processing.run(
                    "native:createspatialindex",
                    {'INPUT': hazard_area_l2_constrained}
                )
                l2_buildings_result = processing.run(
                    "native:joinattributesbylocation",
                    {
                        'INPUT': centroid_layer,
                        'JOIN': hazard_area_l2_constrained,
                        'PREDICATE': [5],  # overlap
                        'JOIN_FIELDS': ['rank'],
                        'METHOD': 0,
                        'DISCARD_NONMATCHING': True,
                        'PREFIX': 'l2_',
                        'OUTPUT': 'TEMPORARY_OUTPUT',
                    },
                )
                l2_buildings = l2_buildings_result['OUTPUT']

            if self.check_canceled():
                return  # キャンセルチェック

            # 津波範囲の建物を取得（全レベル）
            tsunami_buildings = None
            if hazard_area_tsunami_constrained:
                processing.run(
                    "native:createspatialindex",
                    {'INPUT': hazard_area_tsunami_constrained}
                )
                tsunami_buildings_result = processing.run(
                    "native:joinattributesbylocation",
                    {
                        'INPUT': centroid_layer,
                        'JOIN': hazard_area_tsunami_constrained,
                        'PREDICATE': [5],  # overlap
                        'JOIN_FIELDS': ['rank'],
                        'METHOD': 0,
                        'DISCARD_NONMATCHING': True,
                        'PREFIX': 'tsunami_',
                        'OUTPUT': 'TEMPORARY_OUTPUT',
                    },
                )
                tsunami_buildings = tsunami_buildings_result['OUTPUT']


            for year in unique_years:
                if self.check_canceled():
                    return  # キャンセルチェック
                year_field = f"{year}_population"

                # 総人口を集計（集計対象の行政区域でフィルタ）
                total_pop_result = centroid_layer.aggregate(
                    QgsAggregateCalculator.Aggregate.Sum,
                    year_field,
                    QgsAggregateCalculator.AggregateParameters(),
                )
                total_pop = (
                    int(total_pop_result[0])
                    if total_pop_result[0] is not None
                    else 0
                )

                # L1浸水区域内人口（0.5m以上）rank>=2
                flood_plan_0p5m_pop = 0
                flood_plan_3m_pop = 0
                if l1_buildings:
                    for feature in l1_buildings.getFeatures():
                        l1_rank_value = feature['l1_rank']
                        if l1_rank_value is not None:
                            try:
                                rank_int = int(l1_rank_value)
                                if rank_int >= 2:
                                    pop_value = feature[year_field]
                                    if pop_value is not None:
                                        flood_plan_0p5m_pop += int(pop_value)

                                # L1浸水区域内人口（3m以上）rank>=3
                                if rank_int >= 3:
                                    pop_value = feature[year_field]
                                    if pop_value is not None:
                                        flood_plan_3m_pop += int(pop_value)
                            except (ValueError, TypeError):
                                continue

                # L2浸水区域内人口（0.5m以上）rank>=2
                flood_assumed_0p5m_pop = 0
                flood_assumed_3m_pop = 0
                if l2_buildings:
                    for feature in l2_buildings.getFeatures():
                        l2_rank_value = feature['l2_rank']
                        if l2_rank_value is not None:
                            try:
                                rank_int = int(l2_rank_value)
                                if rank_int >= 2:
                                    pop_value = feature[year_field]
                                    if pop_value is not None:
                                        flood_assumed_0p5m_pop += int(pop_value)

                                # L2浸水区域内人口（3m以上）rank>=3
                                if rank_int >= 3:
                                    pop_value = feature[year_field]
                                    if pop_value is not None:
                                        flood_assumed_3m_pop += int(pop_value)
                            except (ValueError, TypeError):
                                continue

                # 津波区域内人口（2m以上）
                tsunami_2m_pop = 0
                if tsunami_buildings:
                    for feature in tsunami_buildings.getFeatures():
                        rank_value = feature['tsunami_rank']
                        if rank_value is not None and isinstance(rank_value, str):
                            try:
                                # 2.0m以上の条件
                                if ('2.0m以上' in rank_value or
                                    '5.0m以上' in rank_value or
                                    '10.0m以上' in rank_value):
                                    pop_value = feature[year_field]
                                    if pop_value is not None:
                                        tsunami_2m_pop += int(pop_value)
                            except (ValueError, TypeError):
                                continue

                # 各人口割合の計算
                flood_plan_0p5m_share = (
                    self.round_or_na(flood_plan_0p5m_pop / total_pop, 3)
                    if total_pop > 0
                    else '―'
                )

                flood_plan_3m_share = (
                    self.round_or_na(flood_plan_3m_pop / total_pop, 3)
                    if total_pop > 0
                    else '―'
                )

                flood_assumed_0p5m_share = (
                    self.round_or_na(flood_assumed_0p5m_pop / total_pop, 3)
                    if total_pop > 0
                    else '―'
                )

                flood_assumed_3m_share = (
                    self.round_or_na(flood_assumed_3m_pop / total_pop, 3)
                    if total_pop > 0
                    else '―'
                )

                tsunami_2m_share = (
                    self.round_or_na(tsunami_2m_pop / total_pop, 3)
                    if total_pop > 0
                    else '―'
                )

                # 前年度のデータがあれば、変化率を計算
                if data_list:
                    previous_year_data = data_list[-1]

                    flood_plan_0p5m_share_delta = (
                        self.round_or_na(flood_plan_0p5m_share - previous_year_data['flood_plan_0p5m_inundation_pop_share'], 2)
                        if isinstance(previous_year_data['flood_plan_0p5m_inundation_pop_share'], (int, float))
                        and isinstance(flood_plan_0p5m_share, (int, float))
                        else '―'
                    )

                    flood_plan_3m_share_delta = (
                        self.round_or_na(flood_plan_3m_share - previous_year_data['flood_plan_3m_inundation_pop_share'], 2)
                        if isinstance(previous_year_data['flood_plan_3m_inundation_pop_share'], (int, float))
                        and isinstance(flood_plan_3m_share, (int, float))
                        else '―'
                    )

                    flood_assumed_0p5m_share_delta = (
                        self.round_or_na(flood_assumed_0p5m_share - previous_year_data['flood_assumed_0p5m_inundation_pop_share'], 2)
                        if isinstance(previous_year_data['flood_assumed_0p5m_inundation_pop_share'], (int, float))
                        and isinstance(flood_assumed_0p5m_share, (int, float))
                        else '―'
                    )

                    flood_assumed_3m_share_delta = (
                        self.round_or_na(flood_assumed_3m_share - previous_year_data['flood_assumed_3m_inundation_pop_share'], 2)
                        if isinstance(previous_year_data['flood_assumed_3m_inundation_pop_share'], (int, float))
                        and isinstance(flood_assumed_3m_share, (int, float))
                        else '―'
                    )

                    tsunami_2m_share_delta = (
                        self.round_or_na(tsunami_2m_share - previous_year_data['tsunami_2m_inundation_pop_share'], 2)
                        if isinstance(previous_year_data['tsunami_2m_inundation_pop_share'], (int, float))
                        and isinstance(tsunami_2m_share, (int, float))
                        else '―'
                    )

                else:
                    flood_plan_0p5m_share_delta = '―'
                    flood_plan_3m_share_delta = '―'
                    flood_assumed_0p5m_share_delta = '―'
                    flood_assumed_3m_share_delta = '―'
                    tsunami_2m_share_delta = '―'

                # データを辞書にまとめる
                year_data = {
                    # 年次
                    'year': year,
                    # 洪水計画_0.5m以上浸水区域人口割合
                    'flood_plan_0p5m_inundation_pop_share': flood_plan_0p5m_share,
                    # 洪水計画_0.5以上浸水区域プロポーション変化
                    'flood_plan_0p5m_inundation_pop_share_delta': flood_plan_0p5m_share_delta if data_list else '―',
                    # 全国平均値
                    'flood_plan_0p5m_inundation_national_avg': '',
                    # 都道府県平均値
                    'flood_plan_0p5m_inundation_pref_avg': '',
                    # 洪水計画_3m以上浸水区域人口割合
                    'flood_plan_3m_inundation_pop_share': flood_plan_3m_share,
                    # 洪水計画_3m以上浸水区域プロポーション変化
                    'flood_plan_3m_inundation_pop_share_delta': flood_plan_3m_share_delta if data_list else '―',
                    # 全国平均値
                    'flood_plan_3m_inundation_national_avg': '',
                    # 都道府県平均値
                    'flood_plan_3m_inundation_pref_avg': '',
                    # 洪水想定_0.5m以上浸水区域人口割合
                    'flood_assumed_0p5m_inundation_pop_share': flood_assumed_0p5m_share,
                    # 洪水想定_0.5m以上浸水区域プロポーション変化
                    'flood_assumed_0p5m_inundation_pop_share_delta': flood_assumed_0p5m_share_delta if data_list else '―',
                    # 全国平均値
                    'flood_assumed_0p5m_inundation_national_avg': '',
                    # 都道府県平均値
                    'flood_assumed_0p5m_inundation_pref_avg': '',
                    # 洪水想定_3m以上浸水区域人口割合
                    'flood_assumed_3m_inundation_pop_share': flood_assumed_3m_share,
                    # 洪水想定_3m以上浸水区域プロポーション変化
                    'flood_assumed_3m_inundation_pop_share_delta': flood_assumed_3m_share_delta if data_list else '―',
                    # 全国平均値
                    'flood_assumed_3m_inundation_national_avg': '',
                    # 都道府県平均値
                    'flood_assumed_3m_inundation_pref_avg': '',
                    # 津波_2m以上浸水区域人口割合
                    'tsunami_2m_inundation_pop_share': tsunami_2m_share,
                    # 津波_2m以上浸水区域プロポーション変化
                    'tsunami_2m_inundation_pop_share_delta': tsunami_2m_share_delta if data_list else '―',
                    # 全国平均値
                    'tsunami_2m_inundation_national_avg': '',
                    # 都道府県平均値
                    'tsunami_2m_inundation_pref_avg': '',
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
                'flood_plan_0p5m_inundation_pop_share': '',
                'flood_plan_0p5m_inundation_pop_share_delta': '',
                'flood_plan_0p5m_inundation_national_avg': '',
                'flood_plan_0p5m_inundation_pref_avg': '',
                'flood_plan_3m_inundation_pop_share': '',
                'flood_plan_3m_inundation_pop_share_delta': '',
                'flood_plan_3m_inundation_national_avg': '',
                'flood_plan_3m_inundation_pref_avg': '',
                'flood_assumed_0p5m_inundation_pop_share': '',
                'flood_assumed_0p5m_inundation_pop_share_delta': '',
                'flood_assumed_0p5m_inundation_national_avg': '',
                'flood_assumed_0p5m_inundation_pref_avg': '',
                'flood_assumed_3m_inundation_pop_share': '',
                'flood_assumed_3m_inundation_pop_share_delta': '',
                'flood_assumed_3m_inundation_national_avg': '',
                'flood_assumed_3m_inundation_pref_avg': '',
                'tsunami_2m_inundation_pop_share': '',
                'tsunami_2m_inundation_pop_share_delta': '',
                'tsunami_2m_inundation_national_avg': '',
                'tsunami_2m_inundation_pref_avg': '',
            }]
        self.export(
            self.base_path + f'\\IF103_防災関連評価指標ファイル{self.file_suffix}.csv',
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
