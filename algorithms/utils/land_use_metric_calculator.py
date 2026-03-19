"""
/***************************************************************************
 *
 * 【FN014】土地利用関連評価指標算出機能
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
)
from PyQt5.QtCore import QCoreApplication
import processing
from .gpkg_manager import GpkgManager


class LandUseMetricCalculator:
    """土地利用関連評価指標算"""
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
            # 行政区域
            zones_layer = self.gpkg_manager.load_layer(
                'zones', None, withload_project=False
            )
            # 変化度マップ
            change_maps_layer = self.gpkg_manager.load_layer(
                'change_maps', None, withload_project=False
            )
            # 用途地域
            land_use_areas_layer = self.gpkg_manager.load_layer(
                'land_use_areas', None, withload_project=False
            )
            # 誘導区域
            induction_layer = self.gpkg_manager.load_layer(
                'induction_areas', None, withload_project=False
            )
            # 土地利用細分化メッシュ
            land_use_meshes_layer = self.gpkg_manager.load_layer(
                'land_use_maps', None, withload_project=False
            )
            # 都市計画区域
            urban_plannings_layer = self.gpkg_manager.load_layer(
                'urban_plannings', None, withload_project=False
            )
            # 仮想居住誘導区域
            hypothetical_residential_layer = self.gpkg_manager.load_layer(
                'hypothetical_residential_areas', None, withload_project=False
            )

            # 必須レイヤのチェック
            missing_layers = []
            if not zones_layer:
                missing_layers.append("zones")
            if not change_maps_layer:
                missing_layers.append("change_maps")

            if missing_layers:
                QgsMessageLog.logMessage(
                    self.tr("Missing layers: %1. Outputting empty result.")
                    .replace("%1", ", ".join(missing_layers)),
                    self.tr("Plugin"),
                    Qgis.Warning,
                )
                self.__export_data([])
                return

            # is_target=1のzonesを抽出
            target_zones_result = processing.run(
                "native:extractbyexpression",
                {
                    'INPUT': zones_layer,
                    'EXPRESSION': '"is_target" = 1',
                    'OUTPUT': 'memory:'
                }
            )
            target_zones_layer = target_zones_result['OUTPUT']

            if target_zones_layer.featureCount() == 0:
                target_zones_layer = None

            # target_zones_layerがない場合は集計を行わない
            if not target_zones_layer:
                self.__export_data([])
                return

            # データリストを作成
            data_list = []

            # 工業専用地域を除外した変化度マップを作成
            # まず行政区域制約を適用
            admin_constrained_change_maps = self.__extract_within_zones(
                change_maps_layer, target_zones_layer
            )

            # 工業専用地域を除外（land_use_areasがある場合）
            if land_use_areas_layer:
                # 工業専用地域と重ならないchange_mapsを抽出
                filtered_change_maps = self.__exclude_industrial_zones(
                    admin_constrained_change_maps, land_use_areas_layer
                )
            else:
                filtered_change_maps = admin_constrained_change_maps

            # 居住誘導区域（type_id=31）を取得、なければ仮想居住誘導区域を使用
            has_residential_area = False
            use_hypothetical_areas = False
            residential_area_layer = None

            if induction_layer:
                residential_induction_result = processing.run(
                    "native:extractbyexpression",
                    {
                        'INPUT': induction_layer,
                        'EXPRESSION': '"type_id" = 31',
                        'OUTPUT': 'memory:'
                    }
                )
                residential_induction_layer = residential_induction_result['OUTPUT']

                if residential_induction_layer.featureCount() > 0:
                    residential_area_layer = residential_induction_layer
                    has_residential_area = True

            # 居住誘導区域がない場合は仮想居住誘導区域を使用
            if not has_residential_area and hypothetical_residential_layer:
                residential_area_layer = hypothetical_residential_layer
                use_hypothetical_areas = True

                QgsMessageLog.logMessage(
                    self.tr("No residential induction areas found. Using hypothetical residential areas."),
                    self.tr("Plugin"),
                    Qgis.Info,
                )

            # 都市計画区域内の宅地利用メッシュ数を取得
            residential_land_mesh_count = self.__get_residential_land_mesh_count(
                land_use_meshes_layer, urban_plannings_layer, target_zones_layer
            )

            # 新築指数、滅失指数、その他指数を計算
            # 居住誘導区域内外でのそれぞれの指数を算出

            # 空間インデックス作成
            processing.run(
                "native:createspatialindex", {'INPUT': filtered_change_maps}
            )
            if residential_area_layer and residential_area_layer.featureCount() > 0:
                processing.run(
                    "native:createspatialindex", {'INPUT': residential_area_layer}
                )

            # 居住誘導区域内の変化度データを取得
            inside_rpa_change_maps = self.__extract_within_induction_areas(
                filtered_change_maps, residential_area_layer
            )

            # 居住誘導区域外の変化度データを取得
            outside_rpa_change_maps = self.__extract_outside_induction_areas(
                filtered_change_maps, residential_area_layer
            )

            # 新築指数（区域内外）- 変化度マップは新築のみ
            new_construction_index_inside_rpa = self.__calculate_construction_index(
                inside_rpa_change_maps, residential_land_mesh_count, 'new_construction'
            )
            new_construction_index_outside_rpa = self.__calculate_construction_index(
                outside_rpa_change_maps, residential_land_mesh_count, 'new_construction'
            )

            # 滅失指数とその他指数
            demolition_index_inside_rpa = ''
            demolition_index_outside_rpa = ''
            other_construction_index_inside_rpa = ''
            other_construction_index_outside_rpa = ''
            # 積算変化度と変化量の差を計算
            new_construction_cumulative_change = self.__calculate_cumulative_change_index(
                inside_rpa_change_maps, outside_rpa_change_maps, 'new_construction'
            )
            # 滅失とその他
            demolition_cumulative_change = ''
            other_construction_cumulative_change = ''

            # 区域内外の変化量の差
            new_construction_delta_gap = self.__calculate_delta_gap(
                new_construction_index_inside_rpa, new_construction_index_outside_rpa
            )
            # 滅失とその他
            demolition_delta_gap = ''
            other_construction_delta_gap = ''

            # データを辞書にまとめる
            year_data = {
                # 年次
                'year': 1,
                # 宅地メッシュ数
                'residential_land_mesh_count': residential_land_mesh_count,
                # 居住誘導区域外建築新築指数
                'new_construction_index_outside_rpa': new_construction_index_outside_rpa,
                # 居住誘導区域内建築新築指数
                'new_construction_index_inside_rpa': new_construction_index_inside_rpa,
                # 積算変化度
                'new_construction_index_cumulative_change_index': new_construction_cumulative_change,
                # 居住誘導区域内外建築新築の変化量の差
                'new_construction_index_building_index_delta_gap_rpa_vs_outside': new_construction_delta_gap,
                # 全国平均値
                'new_construction_index_national_avg': '',
                # 都道府県平均値
                'new_construction_index_pref_avg': '',
                # 居住誘導区域外建築滅失指数
                'demolition_index_outside_rpa': demolition_index_outside_rpa,
                # 居住誘導区域内建築滅失指数
                'demolition_index_inside_rpa': demolition_index_inside_rpa,
                # 積算変化度
                'demolition_index_cumulative_change_index': demolition_cumulative_change,
                # 居住誘導区域内外建築滅失の変化量の差
                'demolition_index_building_index_delta_gap_rpa_vs_outside': demolition_delta_gap,
                # 居住誘導区域外建築その他指数
                'other_construction_index_outside_rpa': other_construction_index_outside_rpa,
                # 居住誘導区域内建築その他指数
                'other_construction_index_inside_rpa': other_construction_index_inside_rpa,
                # 積算変化度
                'other_construction_index_cumulative_change_index': other_construction_cumulative_change,
                # 居住誘導区域内外建築その他の変化量の差
                'other_construction_index_building_index_delta_gap_rpa_vs_outside': other_construction_delta_gap,
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
                'residential_land_mesh_count': '',
                'new_construction_index_outside_rpa': '',
                'new_construction_index_inside_rpa': '',
                'new_construction_index_cumulative_change_index': '',
                'new_construction_index_building_index_delta_gap_rpa_vs_outside': '',
                'new_construction_index_national_avg': '',
                'new_construction_index_pref_avg': '',
                'demolition_index_outside_rpa': '',
                'demolition_index_inside_rpa': '',
                'demolition_index_cumulative_change_index': '',
                'demolition_index_building_index_delta_gap_rpa_vs_outside': '',
                'other_construction_index_outside_rpa': '',
                'other_construction_index_inside_rpa': '',
                'other_construction_index_cumulative_change_index': '',
                'other_construction_index_building_index_delta_gap_rpa_vs_outside': '',
            }]
        self.export(
            self.base_path + f'\\IF105_土地利用関連評価指標ファイル{self.file_suffix}.csv',
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

    def __extract_within_zones(self, target_layer, zones_layer):
        """行政区域内のフィーチャを抽出"""
        processing.run(
            "native:createspatialindex", {'INPUT': target_layer}
        )
        processing.run(
            "native:createspatialindex", {'INPUT': zones_layer}
        )
        result = processing.run(
            "native:extractbylocation",
            {
                'INPUT': target_layer,
                'PREDICATE': [6],  # within
                'INTERSECT': zones_layer,
                'OUTPUT': 'TEMPORARY_OUTPUT',
            },
        )['OUTPUT']
        return result

    def __exclude_industrial_zones(self, change_maps_layer, land_use_areas_layer):
        """工業専用地域を除外"""
        # 工業専用地域フィルタ
        industrial_zones = processing.run(
            "native:extractbyexpression",
            {
                'INPUT': land_use_areas_layer,
                'EXPRESSION': '"land_use_type" = \'工業専用地域\'',
                'OUTPUT': 'TEMPORARY_OUTPUT',
            },
        )['OUTPUT']

        # 空間インデックス作成
        processing.run(
            "native:createspatialindex", {'INPUT': change_maps_layer}
        )
        processing.run(
            "native:createspatialindex", {'INPUT': industrial_zones}
        )

        # 工業専用地域と重ならないchange_mapsを抽出
        result = processing.run(
            "native:extractbylocation",
            {
                'INPUT': change_maps_layer,
                'PREDICATE': [2],  # disjoint
                'INTERSECT': industrial_zones,
                'OUTPUT': 'TEMPORARY_OUTPUT',
            },
        )['OUTPUT']
        return result

    def __get_residential_land_mesh_count(self, land_use_meshes_layer, urban_plannings_layer, target_zones_layer):
        """都市計画区域内の宅地利用メッシュ数を取得"""
        try:
            if not land_use_meshes_layer:
                return 0

            # 都市計画区域がある場合はその範囲内、ない場合は全てのメッシュを対象とする
            if urban_plannings_layer:
                # 空間インデックス作成
                processing.run(
                    "native:createspatialindex",
                    {'INPUT': land_use_meshes_layer}
                )
                processing.run(
                    "native:createspatialindex",
                    {'INPUT': urban_plannings_layer}
                )
                # 都市計画区域内のメッシュを抽出
                urban_meshes = processing.run(
                    "native:extractbylocation",
                    {
                        'INPUT': land_use_meshes_layer,
                        'PREDICATE': [6],  # within
                        'INTERSECT': urban_plannings_layer,
                        'OUTPUT': 'TEMPORARY_OUTPUT',
                    },
                )['OUTPUT']
            else:
                urban_meshes = land_use_meshes_layer

            # 空間インデックス作成
            processing.run(
                "native:createspatialindex", {'INPUT': urban_meshes}
            )

            # 行政区域制約を適用
            admin_constrained_meshes = processing.run(
                "native:extractbylocation",
                {
                    'INPUT': urban_meshes,
                    'PREDICATE': [6],  # within
                    'INTERSECT': target_zones_layer,
                    'OUTPUT': 'TEMPORARY_OUTPUT',
                },
            )['OUTPUT']

            # 建物用地のメッシュを抽出（type=0700）
            residential_meshes = processing.run(
                "native:extractbyexpression",
                {
                    'INPUT': admin_constrained_meshes,
                    'EXPRESSION': '"type" = \'0700\'',
                    'OUTPUT': 'TEMPORARY_OUTPUT',
                },
            )['OUTPUT']

            return residential_meshes.featureCount()
        except Exception:
            return 0

    def __extract_within_induction_areas(self, change_maps_layer, residential_area_layer):
        """居住誘導区域内の変化度データを抽出"""
        if not residential_area_layer or residential_area_layer.featureCount() == 0:
            # 空のレイヤを返す
            empty_layer = QgsVectorLayer(
                "Polygon?crs=" + change_maps_layer.crs().authid(),
                "empty_induction",
                "memory",
            )
            return empty_layer

        result = processing.run(
            "native:extractbylocation",
            {
                'INPUT': change_maps_layer,
                'PREDICATE': [6],  # within
                'INTERSECT': residential_area_layer,
                'OUTPUT': 'TEMPORARY_OUTPUT',
            },
        )['OUTPUT']
        return result

    def __extract_outside_induction_areas(self, change_maps_layer, residential_area_layer):
        """居住誘導区域外の変化度データを抽出"""
        if not residential_area_layer or residential_area_layer.featureCount() == 0:
            return change_maps_layer

        result = processing.run(
            "native:extractbylocation",
            {
                'INPUT': change_maps_layer,
                'PREDICATE': [2],  # disjoint
                'INTERSECT': residential_area_layer,
                'OUTPUT': 'TEMPORARY_OUTPUT',
            },
        )['OUTPUT']
        return result

    def __calculate_construction_index(self, change_maps_layer, residential_land_mesh_count, index_type):
        """建築指数を計算"""
        try:
            if residential_land_mesh_count == 0:
                return ''

            # 変化度別にメッシュ数をカウント
            change_degree_counts = {1: 0, 2: 0, 3: 0, 4: 0}

            # index_typeに応じたフィールド名を決定
            if index_type == 'new_construction':
                field_name = 'level'
            elif index_type == 'demolition':
                field_name = 'demolition_degree'  # 滅失は滅失度フィールド
            else:  # other_construction
                field_name = 'other_degree'  # その他はその他度フィールド

            for feature in change_maps_layer.getFeatures():
                try:
                    degree_value = feature[field_name]
                    try:
                        degree_int = int(degree_value) if degree_value is not None else None
                        if degree_int in change_degree_counts:
                            change_degree_counts[degree_int] += 1
                    except (ValueError, TypeError):
                        continue
                except (KeyError, AttributeError):
                    # フィールドが存在しない場合はスキップ
                    continue

            # 積算変化度を計算: (変化度1のメッシュ数×1) + (変化度2のメッシュ数×2) + ...
            cumulative_change = sum(
                degree * count for degree, count in change_degree_counts.items()
            )

            # 指数 = 積算変化度 / 都市計画区域内の宅地利用メッシュ数
            index = cumulative_change / residential_land_mesh_count if residential_land_mesh_count > 0 else 0
            return self.round_or_na(index, 2)
        except Exception:
            return ''

    def __calculate_cumulative_change_index(self, inside_maps, outside_maps, index_type):
        """積算変化度を計算"""
        # 区域内外の積算変化度を合計
        inside_cumulative = self.__get_cumulative_change(inside_maps, index_type)
        outside_cumulative = self.__get_cumulative_change(outside_maps, index_type)
        total_cumulative = inside_cumulative + outside_cumulative
        return self.round_or_na(total_cumulative, 2)

    def __get_cumulative_change(self, change_maps_layer, index_type):
        """変化度レイヤから積算変化度を計算"""
        if index_type == 'new_construction':
            field_name = 'level'
        elif index_type == 'demolition':
            field_name = 'demolition_degree'
        else:  # other_construction
            field_name = 'other_degree'

        change_degree_counts = {1: 0, 2: 0, 3: 0, 4: 0}

        try:
            for feature in change_maps_layer.getFeatures():
                degree_value = feature[field_name]
                try:
                    degree_int = int(degree_value) if degree_value is not None else None
                    if degree_int in change_degree_counts:
                        change_degree_counts[degree_int] += 1
                except (ValueError, TypeError):
                    continue
        except (KeyError, AttributeError):
            # フィールドが存在しない場合は0を返す
            return 0

        # 積算変化度を計算
        cumulative_change = sum(
            degree * count for degree, count in change_degree_counts.items()
        )
        return cumulative_change

    def __calculate_delta_gap(self, inside_index, outside_index):
        """区域内外の変化量の差を計算"""
        if isinstance(inside_index, (int, float)) and isinstance(outside_index, (int, float)):
            delta_gap = inside_index - outside_index
            return self.round_or_na(delta_gap, 2)
        else:
            return '―'
