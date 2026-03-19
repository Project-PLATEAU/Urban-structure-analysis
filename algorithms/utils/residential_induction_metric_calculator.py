"""
/***************************************************************************
 *
 * 【FN010】居住誘導関連評価指標算出機能
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
    QgsCoordinateReferenceSystem,
)
from PyQt5.QtCore import QCoreApplication
import processing
from .gpkg_manager import GpkgManager


class ResidentialInductionMetricCalculator:
    """居住誘導関連評価指標算出機能"""
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

            # 目標人口
            population_target_settings_layer = self.gpkg_manager.load_layer(
                'population_target_settings', None, withload_project=False
            )

            # 行政区域
            zones_layer = self.gpkg_manager.load_layer(
                'zones', None, withload_project=False
            )

            # 仮想居住誘導区域
            hypothetical_residential_layer = self.gpkg_manager.load_layer(
                'hypothetical_residential_areas', None, withload_project=False
            )

            if not buildings_layer:
                raise Exception(self.tr("The %1 layer was not found.")
                    .replace("%1", "buildings"))

            if not induction_layer:
                raise Exception(self.tr("The %1 layer was not found.")
                    .replace("%1", "induction_areas"))

            if not population_target_settings_layer:
                raise Exception(self.tr("The %1 layer was not found.")
                    .replace("%1", "population_target_settings"))

            if not zones_layer:
                raise Exception(self.tr("The %1 layer was not found.")
                    .replace("%1", "zones"))

            comparative_year = None
            target_population = None

            feature = next(
                population_target_settings_layer.getFeatures(), None)
            if feature:
                comparative_year = feature['comparative_year']
                target_population = feature['target_population']

                msg = self.tr(
                    "Comparative future year: %1, Target population: %2"
                ).replace("%1", str(comparative_year)).replace(
                    "%2", str(target_population)
                )
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Info,
                )
            else:
                msg = self.tr("Target population data was not found.")
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Warning,
                )

            centroid_layer = QgsVectorLayer(
                "Point?crs=" + buildings_layer.crs().authid(),
                "tmp_building_centroids",
                "memory",
            )
            centroid_layer_data = centroid_layer.dataProvider()

            # 元の建物レイヤから属性をコピー
            centroid_layer_data.addAttributes(buildings_layer.fields())
            centroid_layer.updateFields()

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
                    msg = self.tr("Using %1 target zones (is_target=1) for calculation.").replace("%1", str(len(target_zones_features)))
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

            # target_zones_layerがない場合はヘッダーだけのCSVを出力
            if not target_zones_layer:
                msg = self.tr("No target zones available. Returning empty results.")
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Info,
                )
                self.__export_empty_results()
                return

            self.target_zones_layer = target_zones_layer

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

            if self.check_canceled():
                return  # キャンセルチェック

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
                centroids_in_target = joined_result['OUTPUT']
            else:
                # target_zonesがない場合は全重心を使用
                centroids_in_target = centroids_all

            if self.check_canceled():
                return  # キャンセルチェック

            # centroid_layerにフィーチャを追加
            centroid_features = []
            for feature in centroids_in_target.getFeatures():
                centroid_features.append(feature)

            centroid_layer_data.addFeatures(centroid_features)
            centroid_layer.updateExtents()

            # 空間インデックス作成
            processing.run(
                "native:createspatialindex", {'INPUT': centroid_layer}
            )

            centroid_layer = self.gpkg_manager.add_layer(
                centroid_layer, "tmp_building_centroids", None, False
            )
            if not centroid_layer:
                raise Exception(self.tr("Failed to add layer to GeoPackage."))

            self.centroid_layer = centroid_layer

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
            else:
                msg = self.tr("No residential induction area (type_id=31) or hypothetical residential areas found.")
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Warning,
                )
                residential_area_layer = None

            # residential_area_layerがない場合はヘッダーだけのCSVを出力
            if not residential_area_layer:
                msg = self.tr("No residential areas available. Returning empty results.")
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Warning,
                )
                self.__export_empty_results()
                return

            # target_zones_layerがある場合、居住誘導区域をtarget_zonesでクリップ
            if target_zones_layer and residential_area_layer.featureCount() > 0:
                # CRSが異なる場合は再投影してからクリップ
                if residential_area_layer.crs() != target_zones_layer.crs():
                    reprojected = processing.run(
                        "native:reprojectlayer",
                        {
                            'INPUT': residential_area_layer,
                            'TARGET_CRS': target_zones_layer.crs(),
                            'OUTPUT': 'memory:'
                        }
                    )['OUTPUT']
                    residential_area_layer = reprojected

                clipped_residential = processing.run(
                    "native:clip",
                    {
                        'INPUT': residential_area_layer,
                        'OVERLAY': target_zones_layer,
                        'OUTPUT': 'memory:'
                    }
                )['OUTPUT']

                # クリップされたレイヤを使用
                residential_area_layer = clipped_residential

                msg = self.tr("Residential induction areas clipped to target zones.")
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Info,
                )

            # 空間インデックス作成
            processing.run(
                "native:createspatialindex", {'INPUT': residential_area_layer}
            )

            # CRS変換先（EPSG:3857）
            crs_dest = QgsCoordinateReferenceSystem(
                3857
            )  # メートル単位の座標系 (EPSG:3857)

            # 居住誘導区域の面積計算用にCRS変換
            transformed_residential_layer = processing.run(
                "native:reprojectlayer",
                {
                    'INPUT': residential_area_layer,
                    'TARGET_CRS': crs_dest,
                    'OUTPUT': 'memory:',
                },
            )['OUTPUT']

            # 立地適正化計画区域の面積計算用にCRS変換（必要な場合）
            transformed_induction_layer = processing.run(
                "native:reprojectlayer",
                {
                    'INPUT': induction_layer,
                    'TARGET_CRS': crs_dest,
                    'OUTPUT': 'memory:',
                },
            )['OUTPUT']

            # 面積計算
            area = 0  # 居住誘導区域の面積(ha) - target_zones内のみ
            for feature in transformed_residential_layer.getFeatures():
                # 面積計算 (ヘクタール単位へ変換: 1ヘクタール = 10,000平方メートル)
                area += feature.geometry().area() / 10000

            # 立地適正化計画区域の面積計算
            outside_area = 0  # 立地適正化計画区域の面積(ha)
            for induction_feature in transformed_induction_layer.getFeatures():
                # 立地適正化計画区域（type_id=0）
                if induction_feature["type_id"] == 0:
                    # target_zones_layerがある場合はクリップして計算
                    if target_zones_layer:
                        # 個別にクリップして面積を計算
                        for zone_feature in target_zones_layer.getFeatures():
                            intersection = induction_feature.geometry().intersection(zone_feature.geometry())
                            if not intersection.isEmpty():
                                # CRS変換後の座標系で面積計算
                                outside_area += intersection.area() / 10000
                    else:
                        # 面積計算 (ヘクタール単位へ変換)
                        outside_area += induction_feature.geometry().area() / 10000

            # 居住誘導区域内の建物を取得
            result = processing.run(
                "native:joinattributesbylocation",
                {
                    'INPUT': centroid_layer,
                    'JOIN': residential_area_layer,
                    'PREDICATE': [5],  # within
                    'JOIN_FIELDS': [],
                    'METHOD': 0,
                    'OUTPUT': 'TEMPORARY_OUTPUT',
                    'DISCARD_NONMATCHING': True,
                    'PREFIX': 'induction_area_',
                },
            )

            # 結合結果の取得
            residential_buildings = result['OUTPUT']

            for i, year in enumerate(unique_years):
                if self.check_canceled():
                    return  # キャンセルチェック
                area_pop = 0
                outside_area_pop = 0

                year_field = f"{year}_population"

                # 総人口を集計（行政区域 is_target=1 内）
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

                # SUMフィールドの確認
                sum_field_name = f"{year_field}"  # フィールド名
                sum_field_index = residential_buildings.fields().indexFromName(
                    sum_field_name
                )

                # フィールドが存在するか確認
                if sum_field_index == -1:
                    raise Exception(
                        f"集計フィールド {sum_field_name} が見つかりません"
                    )

                # 居住誘導区域内人口
                sum_result = residential_buildings.aggregate(
                    QgsAggregateCalculator.Aggregate.Sum,
                    sum_field_name,
                    QgsAggregateCalculator.AggregateParameters(),
                )
                area_pop = (
                    int(sum_result[0]) if sum_result[0] is not None else 0
                )

                # 居住誘導区域内人口割合（Rate_Pop）
                rate_pop = (
                    self.round_or_na(area_pop / total_pop, 3)
                    if total_pop > 0
                    else 0
                )
                # 居住誘導区域内人口密度を計算
                pop_area_density = (
                    self.round_or_na(area_pop / area, 2) if area > 0 else '―'
                )  # haあたりの人口密度

                # 前年度のデータがあれば、変化率を計算
                if data_list:
                    # 前年度のデータを取得
                    previous_year_data = data_list[-1]
                    previous_total_pop = previous_year_data['pop_share_admin_pop']
                    # 前年度の居住誘導区域内人口割合を取得
                    previous_rate_pop = previous_year_data['trend_vs_past_rpa_pop_share']
                    # 前年度の居住誘導区域内の人口密度
                    previous_pop_area_density = previous_year_data[
                        'pop_density_rpa'
                    ]
                    # 総人口の変化率を計算
                    if isinstance(total_pop, (int, float)) and isinstance(
                        previous_total_pop, (int, float)
                    ):
                        rate_pop_change = (
                            self.round_or_na(
                                (
                                    (total_pop - previous_total_pop)
                                    / previous_total_pop
                                )
                                * 100,
                                1,
                            )
                            if previous_total_pop > 0
                            else '―'
                        )
                    else:
                        rate_pop_change = '―'

                    # 居住誘導区域内人口割合の変化を計算
                    if isinstance(rate_pop, (int, float)) and isinstance(
                        previous_rate_pop, (int, float)
                    ):
                        rate_area_pop_change = self.round_or_na(
                            rate_pop - previous_rate_pop,
                            3,
                        )
                    else:
                        rate_area_pop_change = '―'

                    # 居住誘導区域内人口密度の変化率
                    if isinstance(
                        pop_area_density, (int, float)
                    ) and isinstance(previous_pop_area_density, (int, float)):
                        rate_density_change = (
                            self.round_or_na(
                                (
                                    (
                                        pop_area_density
                                        - previous_pop_area_density
                                    )
                                    / previous_pop_area_density
                                )
                                * 100,
                                1,
                            )
                            if previous_pop_area_density > 0
                            else '―'
                        )
                    else:
                        rate_density_change = '―'

                else:
                    rate_pop_change = '―'
                    rate_area_pop_change = '―'
                    rate_density_change = '―'
                    pop_outside_rate_density_change = '―'

                # 最後の年度だけ将来人口関連の計算を行う
                if i == len(unique_years) - 1:
                    # 居住誘導区域内将来人口差（p）
                    sum_result = residential_buildings.aggregate(
                        QgsAggregateCalculator.Aggregate.Sum,
                        f"future_{comparative_year}_PT0",
                        QgsAggregateCalculator.AggregateParameters(),
                    )
                    future_area_pop = (
                        int(sum_result[0]) if sum_result[0] is not None else 0
                    )

                    # 現況人口と将来人口から、居住誘導区域内の減少人口：p を求める
                    area_pop_difference = area_pop - future_area_pop

                    # 市内将来人口
                    sum_result = buildings_layer.aggregate(
                        QgsAggregateCalculator.Aggregate.Sum,
                        f"future_{comparative_year}_PT0",
                        QgsAggregateCalculator.AggregateParameters(),
                    )
                    future_total_pop = (
                        int(total_pop_result[0])
                        if total_pop_result[0] is not None
                        else 0
                    )

                    # 市内将来人口と居住誘導区域将来人口から居住誘導区域外の将来人口：rを求める
                    outside_area_future_Pop = future_total_pop - future_area_pop

                    # 誘導目標人口（目標人口と将来人口の差）：Sの割合
                    pop_s = (
                        future_area_pop - target_population
                    )  # S: 目標人口と将来人口の差
                    if target_population > 0:
                        rate_target_pop_difference = (
                            pop_s / target_population
                        ) * 100
                    else:
                        rate_target_pop_difference = '―'

                    # 居住誘導区域の適切さ（S/p）
                    if area_pop_difference != 0:
                        rate_area_appropriateness_sp = (
                            pop_s / area_pop_difference
                        ) * 100
                    else:
                        rate_area_appropriateness_sp = '―'

                    # 居住誘導区域の適切さ（S/r）
                    if outside_area_future_Pop > 0:
                        rate_area_appropriateness_sr = (
                            pop_s / outside_area_future_Pop
                        ) * 100
                    else:
                        rate_area_appropriateness_sr = '―'
                else:
                    # 最終年度以外は '―'
                    area_pop_difference = '―'
                    rate_target_pop_difference = '―'
                    outside_area_future_Pop = '―'
                    rate_area_appropriateness_sp = '―'
                    rate_area_appropriateness_sr = '―'

                # データを辞書にまとめる
                year_data = {
                    'year': year,
                    'pop_share_rpa_pop': area_pop,  # 居住誘導区域内人口
                    'pop_share_rsma_pop': '',  # 居住状況把握対象区域内人口（空白）
                    'pop_share_rpa_pop_sheet_a': '',  # A面記載の居住誘導区域内人口（空白）
                    'pop_share_admin_pop': total_pop,  # 行政区域人口
                    'pop_share_none': rate_pop,  # 人口割合
                    'pop_share_rpa_delta': '',  # 人口割合増減（空白）
                    'pop_share_rpa_national_avg': '',  # 全国平均（空白）
                    'pop_share_rpa_national_sd': '',  # 全国標準偏差（空白）
                    'pop_share_rpa_pref_avg': '',  # 都道府県平均（空白）
                    'pop_share_rpa_pref_sd': '',  # 都道府県標準偏差（空白）
                    'pop_share_rpa_pop_delta_rate': rate_area_pop_change if 'rate_area_pop_change' in locals() else '',  # 居住誘導区域内人口割合変化
                    'trend_vs_past_rpa_pop_2010': '',  # 居住誘導区域内人口（2010）（空白）
                    'trend_vs_past_rsma_pop_2010': '',  # 居住状況把握対象区域内人口（2010）（空白）
                    'trend_vs_past_rpa_pop_2020_sheet_a': '',  # A面記載の居住誘導区域内人口（2020）（空白）
                    'trend_vs_past_admin_pop_2010': '',  # 行政区域人口（2010）（空白）
                    'trend_vs_past_rpa_pop_share': rate_pop,  # 居住誘導区域内人口割合
                    'pop_density_rpa': pop_area_density,  # 居住誘導区域内人口密度
                    'pop_density_rsma': '',  # 居住状況把握対象区域内人口密度（空白）
                    'pop_density_rpa_sheet_a': '',  # A面記載の居住誘導区域内人口密度（空白）
                    'use_hypothetical_areas': 1 if use_hypothetical_areas else 0,  # 仮想居住誘導区域使用フラグ
                }

                # 辞書をリストに追加
                data_list.append(year_data)

            # エクスポート（空の場合はヘッダーだけのCSVを出力）
            self.__export_if101_data(data_list)

            # IF107 将来人口と目標人口の関係性ファイルを算出出力
            self.calc_future_target_population_relationship()

            return

        except Exception as e:
            # エラーメッセージのログ出力
            QgsMessageLog.logMessage(
                self.tr("An error occurred: %1").replace("%1", str(e)),
                self.tr("Plugin"),
                Qgis.Critical,
            )
            raise e

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

    def calc_future_target_population_relationship(self):
        """将来人口と目標人口の関係性を算出"""
        try:
            # 建物レイヤを読み込み
            buildings_layer = self.gpkg_manager.load_layer(
                'buildings', None, withload_project=False
            )
            # 居住誘導区域レイヤを読み込み
            induction_layer = self.gpkg_manager.load_layer(
                'induction_areas', None, withload_project=False
            )
            # 目標人口設定レイヤを読み込み
            target_settings_layer = self.gpkg_manager.load_layer(
                'population_target_settings', None, withload_project=False
            )

            if not buildings_layer:
                raise Exception(self.tr("The %1 layer was not found.")
                    .replace("%1", "buildings"))
            if not induction_layer:
                raise Exception(self.tr("The %1 layer was not found.")
                    .replace("%1", "induction_areas"))
            if not target_settings_layer:
                raise Exception(self.tr("The %1 layer was not found.")
                    .replace("%1", "population_target_settings"))

            centroid_layer = self.centroid_layer
            target_zones_layer = self.target_zones_layer

            # 仮想居住誘導区域レイヤを読み込み
            hypothetical_residential_layer = self.gpkg_manager.load_layer(
                'hypothetical_residential_areas', None, withload_project=False
            )

            # 居住誘導区域（type_id=31）を取得、なければ仮想居住誘導区域を使用
            has_residential_area = False
            use_hypothetical_areas = False
            rpa_features = []

            for feature in induction_layer.getFeatures():
                if feature["type_id"] == 31:
                    rpa_features.append(feature)
                    has_residential_area = True

            # 居住誘導区域がある場合は新しいレイヤを作成
            if has_residential_area:
                rpa_layer = QgsVectorLayer(
                    "Polygon?crs=" + induction_layer.crs().authid(),
                    "rpa",
                    "memory",
                )
                rpa_data = rpa_layer.dataProvider()
                rpa_data.addAttributes(induction_layer.fields())
                rpa_layer.updateFields()
                rpa_data.addFeatures(rpa_features)
                rpa_layer.updateExtents()
            # 居住誘導区域がない場合は仮想居住誘導区域をそのまま使用
            elif hypothetical_residential_layer:
                msg = self.tr("No residential induction area (type_id=31) found. Using hypothetical residential areas.")
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Info,
                )
                rpa_layer = hypothetical_residential_layer
                use_hypothetical_areas = True
            else:
                msg = self.tr("No residential induction area (type_id=31) or hypothetical residential areas found.")
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Warning,
                )
                # 空のレイヤを作成
                rpa_layer = QgsVectorLayer(
                    "Polygon?crs=" + induction_layer.crs().authid(),
                    "rpa",
                    "memory",
                )
                rpa_layer.updateExtents()

            # target_zones_layerがある場合、居住誘導区域をtarget_zonesでクリップ
            if target_zones_layer and rpa_layer.featureCount() > 0:
                # CRSが異なる場合は再投影してからクリップ
                if rpa_layer.crs() != target_zones_layer.crs():
                    reprojected = processing.run(
                        "native:reprojectlayer",
                        {
                            'INPUT': rpa_layer,
                            'TARGET_CRS': target_zones_layer.crs(),
                            'OUTPUT': 'memory:'
                        }
                    )['OUTPUT']
                    rpa_layer = reprojected

                clipped_rpa = processing.run(
                    "native:clip",
                    {
                        'INPUT': rpa_layer,
                        'OVERLAY': target_zones_layer,
                        'OUTPUT': 'memory:'
                    }
                )['OUTPUT']

                # クリップされたレイヤを使用
                rpa_layer = clipped_rpa

                msg = self.tr("Residential induction areas clipped to target zones.")
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Info,
                )

            # 空間インデックス作成
            processing.run("native:createspatialindex", {'INPUT': rpa_layer})

            # 年度情報を取得
            fields = buildings_layer.fields()
            years = set()
            pattern = re.compile(r'^(\d{4})_')
            for field in fields:
                match = pattern.match(field.name())
                if match:
                    years.add(match.group(1))
            unique_years = sorted(list(years))
            latest_year = unique_years[-1] if unique_years else None

            # population_target_settingsから比較年度を取得
            comparative_year = None
            for feature in target_settings_layer.getFeatures():
                comp_year = feature['comparative_year'] if 'comparative_year' in feature.fields().names() else None
                if comp_year is not None:
                    comparative_year = str(comp_year)
                    break

            # 将来人口フィールドを構築 (future_YYYY_PT0)
            future_field = None
            if comparative_year:
                future_field = f'future_{comparative_year}_PT00'

            # 行政区域内の建物重心（最新年人口）を集計
            admin_pop = 0
            if latest_year:
                pop_field = f'{latest_year}_population'
                if pop_field in centroid_layer.fields().names():
                    admin_pop_result = centroid_layer.aggregate(
                        QgsAggregateCalculator.Aggregate.Sum,
                        pop_field,
                        QgsAggregateCalculator.AggregateParameters(),
                    )
                    admin_pop = int(admin_pop_result[0]) if admin_pop_result[0] is not None else 0

            # 行政区域内の建物重心（将来人口）を集計
            municipality_projected_pop = 0
            if future_field and future_field in centroid_layer.fields().names():
                municipality_projected_pop_result = centroid_layer.aggregate(
                    QgsAggregateCalculator.Aggregate.Sum,
                    future_field,
                    QgsAggregateCalculator.AggregateParameters(),
                )
                municipality_projected_pop = int(municipality_projected_pop_result[0]) if municipality_projected_pop_result[0] is not None else 0

            # 居住誘導区域内の建物を取得
            rpa_buildings_result = processing.run(
                "native:joinattributesbylocation",
                {
                    'INPUT': centroid_layer,
                    'JOIN': rpa_layer,
                    'PREDICATE': [5],  # overlap
                    'JOIN_FIELDS': [],
                    'METHOD': 0,
                    'OUTPUT': 'TEMPORARY_OUTPUT',
                    'DISCARD_NONMATCHING': True,
                    'PREFIX': 'rpa_',
                },
            )
            rpa_buildings = rpa_buildings_result['OUTPUT']

            # 居住誘導区域内人口（最新年）を集計
            rpa_pop_sheet_a = 0
            if latest_year:
                pop_field = f'{latest_year}_population'
                if pop_field in rpa_buildings.fields().names():
                    rpa_pop_result = rpa_buildings.aggregate(
                        QgsAggregateCalculator.Aggregate.Sum,
                        pop_field,
                        QgsAggregateCalculator.AggregateParameters(),
                    )
                    rpa_pop_sheet_a = int(rpa_pop_result[0]) if rpa_pop_result[0] is not None else 0

            # 居住誘導区域内将来人口を集計
            rpa_projected_pop = 0
            if future_field and future_field in rpa_buildings.fields().names():
                rpa_projected_pop_result = rpa_buildings.aggregate(
                    QgsAggregateCalculator.Aggregate.Sum,
                    future_field,
                    QgsAggregateCalculator.AggregateParameters(),
                )
                rpa_projected_pop = int(rpa_projected_pop_result[0]) if rpa_projected_pop_result[0] is not None else 0

            # 居住誘導区域外人口を算出
            outside_rpa_pop_sheet_a = admin_pop - rpa_pop_sheet_a

            # 居住誘導区域外将来人口を算出
            outside_rpa_projected_pop = municipality_projected_pop - rpa_projected_pop

            # 目標人口を取得
            rpa_pop_target = 0
            for feature in target_settings_layer.getFeatures():
                target_pop = feature['target_population'] if 'target_population' in feature.fields().names() else 0
                if target_pop is not None:
                    rpa_pop_target = round(target_pop) if isinstance(target_pop, (int, float)) else 0
                    break

            # 居住誘導区域外の目標人口を算出
            outside_rpa_pop_target = round(municipality_projected_pop - rpa_pop_target)

            # データを作成
            data_list = [{
                # 行政区域人口
                'admin_pop': admin_pop,
                # 都市計画区域人口
                'city_planning_area_pop': '',
                # 市街化区域人口
                'urbanization_promotion_area_pop': '',
                # 用途地域人口
                'zoning_pop': '',
                # 用途地域人口（工業・工専のみ）
                'zoning_pop_industrial_only': '',
                # 用途地域（工業・工専除く）人口
                'zoning_pop_excl_industrial': '',
                # 居住誘導区域人口（GIS算出）
                'rpa_pop_gis_estimate': '',
                # 居住誘導区域人口（R2・意向調査より）
                'rpa_pop_from_intention_survey_r2': '',
                # 居住誘導区域人口（A面記載）
                'rpa_pop_sheet_a': rpa_pop_sheet_a,
                # 居住誘導区域外人口
                'outside_rpa_pop_sheet_a': outside_rpa_pop_sheet_a,
                # 都市機能誘導区域人口
                'ufia_pop': '',
                # 居住状況把握対象区域人口
                'rsma_pop': '',
                # 行政区域面積
                'admin_area': '',
                # 都市計画区域面積
                'city_planning_area': '',
                # 市街化区域面積
                'urbanization_promotion_area': '',
                # 用途地域面積
                'zoning_area': '',
                # 用途地域（工業・工専除く）面積
                'zoning_area_excl_industrial': '',
                # 居住誘導区域面積
                'rpa_area': '',
                # 居住誘導区域面積（意向調査より）
                'rpa_area_from_intention_survey': '',
                # 居住誘導区域面積（A面記載）
                'rpa_area_sheet_a': '',
                # 都市機能誘導区域面積
                'ufia_area': '',
                # 都市機能誘導区域面積（意向調査より）
                'ufia_area_from_intention_survey': '',
                # 都市機能誘導区域面積（A面記載）
                'ufia_area_sheet_a': '',
                # 居住状況把握対象区域面積
                'rsma_area': '',
                # 自治体将来人口
                'municipality_projected_pop': municipality_projected_pop,
                # 目標年次
                'target_year': '',
                # 居住誘導区域の人口目標値
                'rpa_pop_target': rpa_pop_target,
                # 居住誘導区域外の目標人口
                'outside_rpa_pop_target': outside_rpa_pop_target,
                # 居住誘導区域人口（2020）
                'rpa_pop_2020': '',
                # 居住誘導区域内の将来人口
                'rpa_projected_pop': rpa_projected_pop,
                # 居住誘導区域外の将来人口
                'outside_rpa_projected_pop': outside_rpa_projected_pop,
                # 必要誘導人口
                'required_induced_pop': '',
                # 居住誘導区域内の人口減少に対する、必要誘導人口の割合
                'required_induced_pop_share_of_rpa_pop_decline': '',
                # 居住誘導区域外の将来人口に対する、必要誘導人口の割合
                'required_induced_pop_share_of_outside_rpa_projected_pop': '',
                # 当該市町村内で目標達成しようとした場合の市町村タイプ
                'municipality_type_for_target_achievement_within_municipality': '',
                # 転入超過数（国内＋国外）5年平均
                'net_in_migration_total_five_year_avg': '',
                # 転入超過数（国内＋国外）（2020）
                'net_in_migration_total_2020': '',
                # 転入超過数（国内＋国外）（2021）
                'net_in_migration_total_2021': '',
                # 転入超過数（国内＋国外）（2022）
                'net_in_migration_total_2022': '',
                # 転入超過数（国内＋国外）（2023）
                'net_in_migration_total_2023': '',
                # 転入超過数（国内＋国外）（2024）
                'net_in_migration_total_2024': '',
                # 転入超過数（国内）5年平均
                'net_in_migration_domestic_five_year_avg': '',
                # 転入超過数（国内）（2020）
                'net_in_migration_domestic_2020': '',
                # 転入超過数（国内）（2021）
                'net_in_migration_domestic_2021': '',
                # 転入超過数（国内）（2022）
                'net_in_migration_domestic_2022': '',
                # 転入超過数（国内）（2023）
                'net_in_migration_domestic_2023': '',
                # 転入超過数（国内）（2024）
                'net_in_migration_domestic_2024': '',
            }]

            # ファイルパスを指定してエクスポート
            self.export(
                self.base_path + f'\\IF107_将来人口と目標人口の関係性ファイル{self.file_suffix}.csv',
                data_list,
            )

        except Exception as e:
            QgsMessageLog.logMessage(
                self.tr("An error occurred in calc_future_target_population_relationship: %1").replace("%1", str(e)),
                self.tr("Plugin"),
                Qgis.Critical,
            )
            raise e

    def __export_if101_data(self, data_list):
        """IF101データをCSVにエクスポート（空の場合はヘッダーだけのCSVを出力）"""
        if not data_list:
            data_list = [{
                'year': '',
                'pop_share_rpa_pop': '',
                'pop_share_rsma_pop': '',
                'pop_share_rpa_pop_sheet_a': '',
                'pop_share_admin_pop': '',
                'pop_share_none': '',
                'pop_share_rpa_delta': '',
                'pop_share_rpa_national_avg': '',
                'pop_share_rpa_national_sd': '',
                'pop_share_rpa_pref_avg': '',
                'pop_share_rpa_pref_sd': '',
                'pop_share_rpa_pop_delta_rate': '',
                'trend_vs_past_rpa_pop_2010': '',
                'trend_vs_past_rsma_pop_2010': '',
                'trend_vs_past_rpa_pop_2020_sheet_a': '',
                'trend_vs_past_admin_pop_2010': '',
                'trend_vs_past_rpa_pop_share': '',
                'pop_density_rpa': '',
                'pop_density_rsma': '',
                'pop_density_rpa_sheet_a': '',
                'use_hypothetical_areas': '',
            }]
        self.export(
            self.base_path + f'\\IF101_居住誘導区域関連評価指標ファイル{self.file_suffix}.csv',
            data_list,
        )

    def __export_empty_results(self):
        """データなし時にIF101・IF107のヘッダーだけのCSVを出力"""
        # IF101
        self.__export_if101_data([])
        # IF107
        empty_if107 = [{
            'admin_pop': '',
            'city_planning_area_pop': '',
            'urbanization_promotion_area_pop': '',
            'zoning_pop': '',
            'zoning_pop_industrial_only': '',
            'zoning_pop_excl_industrial': '',
            'rpa_pop_gis_estimate': '',
            'rpa_pop_from_intention_survey_r2': '',
            'rpa_pop_sheet_a': '',
            'outside_rpa_pop_sheet_a': '',
            'ufia_pop': '',
            'rsma_pop': '',
            'admin_area': '',
            'city_planning_area': '',
            'urbanization_promotion_area': '',
            'zoning_area': '',
            'zoning_area_excl_industrial': '',
            'rpa_area': '',
            'rpa_area_from_intention_survey': '',
            'rpa_area_sheet_a': '',
            'ufia_area': '',
            'ufia_area_from_intention_survey': '',
            'ufia_area_sheet_a': '',
            'rsma_area': '',
            'municipality_projected_pop': '',
            'target_year': '',
            'rpa_pop_target': '',
            'outside_rpa_pop_target': '',
            'rpa_pop_2020': '',
            'rpa_projected_pop': '',
            'outside_rpa_projected_pop': '',
            'required_induced_pop': '',
            'required_induced_pop_share_of_rpa_pop_decline': '',
            'required_induced_pop_share_of_outside_rpa_projected_pop': '',
            'municipality_type_for_target_achievement_within_municipality': '',
            'net_in_migration_total_five_year_avg': '',
            'net_in_migration_total_2020': '',
            'net_in_migration_total_2021': '',
            'net_in_migration_total_2022': '',
            'net_in_migration_total_2023': '',
            'net_in_migration_total_2024': '',
            'net_in_migration_domestic_five_year_avg': '',
            'net_in_migration_domestic_2020': '',
            'net_in_migration_domestic_2021': '',
            'net_in_migration_domestic_2022': '',
            'net_in_migration_domestic_2023': '',
            'net_in_migration_domestic_2024': '',
        }]
        self.export(
            self.base_path + f'\\IF107_将来人口と目標人口の関係性ファイル{self.file_suffix}.csv',
            empty_if107,
        )

    def round_or_na(self, value, decimal_places, threshold=None):
        """丸め処理"""
        if value is None or (threshold is not None and value <= threshold):
            return '―'
        else:
            return round(value, decimal_places)
