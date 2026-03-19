"""
/***************************************************************************
 *
 * 【FN013】公共交通関連評価指標算出機能
 *
 ***************************************************************************/
"""

import re
import csv
from qgis.core import (
    QgsMessageLog,
    Qgis,
    QgsVectorLayer,
    QgsFeature,
    QgsAggregateCalculator,
)
from PyQt5.QtCore import QCoreApplication
import processing
from .gpkg_manager import GpkgManager


class PublicTransportMetricCalculator:
    """公共交通関連評価指標算出"""
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
            # 行政区域
            zones_layer = self.gpkg_manager.load_layer(
                'zones', None, withload_project=False
            )
            # 鉄道カバー圏域
            railway_station_buffers_layer = self.gpkg_manager.load_layer(
                'railway_station_buffers', None, withload_project=False
            )
            # バスカバー圏域
            bus_stop_buffers_layer = self.gpkg_manager.load_layer(
                'bus_stop_buffers', None, withload_project=False
            )

            if not buildings_layer:
                raise Exception(self.tr("The %1 layer was not found.")
                    .replace("%1", "buildings"))

            if not zones_layer:
                raise Exception(self.tr("The %1 layer was not found.")
                    .replace("%1", "zones"))

            if not railway_station_buffers_layer:
                raise Exception(self.tr("The %1 layer was not found.")
                    .replace("%1", "railway_station_buffers"))

            if not bus_stop_buffers_layer:
                raise Exception(self.tr("The %1 layer was not found.")
                    .replace("%1", "bus_stop_buffers"))

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

            # 属性名を取得
            fields = buildings_layer.fields()
            buildings_layer = None

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

            # 市内、鉄道カバー圏の建物を取得
            railway_buildings = self.__extract(
                centroid_layer, railway_station_buffers_layer
            )

            # 市内、バスカバー圏の建物を取得
            bus_buildings = self.__extract(
                centroid_layer, bus_stop_buffers_layer
            )




            for year in unique_years:
                if self.check_canceled():
                    return  # キャンセルチェック
                year_field = f"{year}_population"

                # 行政区域制約付きの総人口を集計
                total_pop = self.__aggregate_sum(centroid_layer, year_field)

                # 鉄道カバー圏人口
                rail_pop_covered = self.__aggregate_sum(railway_buildings, year_field)

                # バスカバー圏人口
                bus_pop_covered = self.__aggregate_sum(bus_buildings, year_field)

                # 公共交通カバー圏人口（鉄道またはバス）- ユニオン処理
                # 鉄道とバスカバー圏をユニオンして重複を除去
                railway_count = railway_buildings.featureCount()
                bus_count = bus_buildings.featureCount()

                if railway_count > 0 and bus_count > 0:
                    # 両方にフィーチャがある場合はユニオン
                    union_result = processing.run(
                        "native:union",
                        {
                            'INPUT': railway_buildings,
                            'OVERLAY': bus_buildings,
                            'OVERLAY_FIELDS_PREFIX': '',
                            'OUTPUT': 'memory:'
                        }
                    )
                    union_layer = union_result['OUTPUT']
                elif railway_count > 0:
                    # 鉄道のみ
                    union_layer = railway_buildings
                elif bus_count > 0:
                    # バスのみ
                    union_layer = bus_buildings
                else:
                    # 両方とも空
                    union_layer = railway_buildings

                # ユニオン結果から人口を集計
                transit_pop_covered = self.__aggregate_sum(union_layer, year_field)


                # 鉄道カバー率
                rail_pop_coverage = (
                    self.round_or_na(rail_pop_covered / total_pop, 3)
                    if total_pop > 0
                    else '―'
                )

                # バスカバー率
                bus_pop_coverage = (
                    self.round_or_na(bus_pop_covered / total_pop, 3)
                    if total_pop > 0
                    else '―'
                )

                # 交通共通カバー率（公共交通カバー率）
                transit_pop_coverage = (
                    self.round_or_na(transit_pop_covered / total_pop, 3)
                    if total_pop > 0
                    else '―'
                )

                # 前年度のデータがあれば、変化率を計算
                if data_list:
                    previous_year_data = data_list[-1]


                    rail_pop_coverage_delta = (
                        self.round_or_na(rail_pop_coverage - previous_year_data['rail_pop_coverage'], 2)
                        if isinstance(previous_year_data['rail_pop_coverage'], (int, float))
                        and isinstance(rail_pop_coverage, (int, float))
                        else '―'
                    )

                    bus_pop_coverage_delta = (
                        self.round_or_na(bus_pop_coverage - previous_year_data['bus_pop_coverage'], 2)
                        if isinstance(previous_year_data['bus_pop_coverage'], (int, float))
                        and isinstance(bus_pop_coverage, (int, float))
                        else '―'
                    )

                    transit_pop_coverage_delta = (
                        self.round_or_na(transit_pop_coverage - previous_year_data['transit_pop_coverage'], 2)
                        if isinstance(previous_year_data['transit_pop_coverage'], (int, float))
                        and isinstance(transit_pop_coverage, (int, float))
                        else '―'
                    )

                else:
                    rail_pop_coverage_delta = '―'
                    bus_pop_coverage_delta = '―'
                    transit_pop_coverage_delta = '―'

                # データを辞書にまとめる
                year_data = {
                    # 年次
                    'year': year,
                    # 公共交通徒歩圏人口カバー率
                    'transit_walk_pop_coverage': '',
                    # 徒歩圏人口カバー率の増減
                    'transit_walk_pop_coverage_delta': '',
                    # 全国平均値
                    'transit_walk_pop_coverage_national_avg': '',
                    # 都道府県平均値
                    'transit_walk_pop_coverage_pref_avg': '',
                    # 鉄道カバー人口
                    'rail_pop_covered': rail_pop_covered,
                    # 鉄道カバー率
                    'rail_pop_coverage': rail_pop_coverage,
                    # 鉄道カバー率増減
                    'rail_pop_coverage_delta': rail_pop_coverage_delta if data_list else '―',
                    # 全国平均値
                    'rail_pop_coverage_national_avg': '',
                    # 都道府県平均値
                    'rail_pop_coverage_pref_avg': '',
                    # バスカバー人口
                    'bus_pop_covered': bus_pop_covered,
                    # バスカバー率
                    'bus_pop_coverage': bus_pop_coverage,
                    # バスカバー率増減
                    'bus_pop_coverage_delta': bus_pop_coverage_delta if data_list else '―',
                    # 全国平均値
                    'bus_pop_coverage_national_avg': '',
                    # 都道府県平均値
                    'bus_pop_coverage_pref_avg': '',
                    # 交通共通カバー人口
                    'transit_pop_covered': transit_pop_covered,
                    # 交通共通カバー率
                    'transit_pop_coverage': transit_pop_coverage,
                    # 交通共通カバー率増減
                    'transit_pop_coverage_delta': transit_pop_coverage_delta if data_list else '―',
                    # 全国平均値
                    'transit_pop_coverage_national_avg': '',
                    # 都道府県平均値
                    'transit_pop_coverage_pref_avg': '',
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
                'transit_walk_pop_coverage': '',
                'transit_walk_pop_coverage_delta': '',
                'transit_walk_pop_coverage_national_avg': '',
                'transit_walk_pop_coverage_pref_avg': '',
                'rail_pop_covered': '',
                'rail_pop_coverage': '',
                'rail_pop_coverage_delta': '',
                'rail_pop_coverage_national_avg': '',
                'rail_pop_coverage_pref_avg': '',
                'bus_pop_covered': '',
                'bus_pop_coverage': '',
                'bus_pop_coverage_delta': '',
                'bus_pop_coverage_national_avg': '',
                'bus_pop_coverage_pref_avg': '',
                'transit_pop_covered': '',
                'transit_pop_coverage': '',
                'transit_pop_coverage_delta': '',
                'transit_pop_coverage_national_avg': '',
                'transit_pop_coverage_pref_avg': '',
            }]
        self.export(
            self.base_path + f'\\IF104_公共交通関連評価指標ファイル{self.file_suffix}.csv',
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

    def __extract(self, target_layer, buffer_layer):
        """バッファレイヤ内に存在するフィーチャを抽出"""
        # 空間インデックスの作成
        processing.run("native:createspatialindex", {'INPUT': buffer_layer})

        # バッファ内のフィーチャを抽出
        result = processing.run(
            "native:extractbylocation",
            {
                'INPUT': target_layer,
                'PREDICATE': [6],  # within
                'INTERSECT': buffer_layer,
                'OUTPUT': 'TEMPORARY_OUTPUT',
            },
        )['OUTPUT']

        return result

    def __aggregate_sum(self, target_layer, sum_field, condition=None):
        """
        条件に基づいて集計を行う
        :param target_layer: 対象のレイヤ
        :param sum_field: 集計するフィールド名
        :param condition: フィルタリングする条件 (QgsExpression 形式の条件式)
        :return: 集計結果
        """
        # 条件がある場合はフィルタリング
        if condition is not None:
            # フィルタリングされたレイヤを作成
            target_layer.setSubsetString(condition)

        # 集計
        result = target_layer.aggregate(
            QgsAggregateCalculator.Aggregate.Sum, sum_field
        )
        try:
            result = int(result[0]) if result[0] is not None else 0
        except (ValueError, TypeError):
            result = 0

        # フィルタ解除
        if condition is not None:
            target_layer.setSubsetString('')

        return result
