"""
/***************************************************************************
 *
 * 【FN007】圏域作成機能
 *
 ***************************************************************************/
"""

import os
import traceback
import heapq

import processing
import chardet
from qgis.core import (
    QgsMessageLog,
    Qgis,
    QgsVectorLayer,
    QgsField,
    QgsFeature,
    QgsProject,
    QgsCoordinateTransform,
    QgsCoordinateReferenceSystem,
    QgsGeometry,
    QgsPointXY,
    QgsWkbTypes,
    QgsFeatureRequest,
)
from qgis.analysis import QgsGraphBuilder
from PyQt5.QtCore import QCoreApplication, QVariant
from PyQt5.QtWidgets import QApplication
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from .gpkg_manager import GpkgManager

class AreaDataGenerator:
    """圏域作成機能"""
    def __init__(
        self,
        base_path,
        threshold_bus,
        threshold_railway,
        threshold_shelter,
        check_canceled_callback=None,
        gpkg_manager=None,
        is_after_change=False,
        induction_area_folder=None,
    ):
        # GeoPackageマネージャーを初期化
        self.gpkg_manager = gpkg_manager
        # インプットデータパス
        self.base_path = base_path
        # 閾値の設定
        self.threshold_bus = float(threshold_bus)
        self.threshold_railway = float(threshold_railway)
        self.threshold_shelter = float(threshold_shelter)

        self.check_canceled = check_canceled_callback

        # 変更後モードフラグ
        self.is_after_change = is_after_change
        # 変更後誘導区域フォルダパス
        self.induction_area_folder = induction_area_folder

    def tr(self, message):
        """翻訳用のメソッド"""
        return QCoreApplication.translate(self.__class__.__name__, message)

    def create_area_data(self):
        """圏域作成処理"""
        # 変更後モードの場合は、誘導区域の更新のみ実行
        if self.is_after_change:
            self.update_induction_area()
            return

        # 変更前モードの場合は全処理を実行
        self.create_station_coverage_area()

        if self.check_canceled():
            return  # キャンセルチェック
        self.create_bus_stop_coverage_area()

        if self.check_canceled():
            return  # キャンセルチェック
        self.create_shelter()

        if self.check_canceled():
            return  # キャンセルチェック
        self.create_shelter_area()

        if self.check_canceled():
            return  # キャンセルチェック
        self.create_urban_function_induction_area()

        if self.check_canceled():
            return  # キャンセルチェック
        self.create_hypothetical_residential_areas()

        # 誘導区域と仮想居住誘導区域の両方が空かチェック
        if self.check_canceled():
            return  # キャンセルチェック
        self.validate_induction_areas()

        if self.check_canceled():
            return  # キャンセルチェック
        self.create_land_use_maps()

        if self.check_canceled():
            return  # キャンセルチェック
        self.create_change_maps()

        if self.check_canceled():
            return  # キャンセルチェック
        self.create_urban_planning_area()

        if self.check_canceled():
            return  # キャンセルチェック
        self.create_land_use_area()

        if self.check_canceled():
            return  # キャンセルチェック
        self.create_hazard_area_planned_scale()

        if self.check_canceled():
            return  # キャンセルチェック
        self.create_hazard_area_max_scale()

        if self.check_canceled():
            return  # キャンセルチェック
        self.create_hazard_area_storm_surge()

        if self.check_canceled():
            return  # キャンセルチェック
        self.create_hazard_area_tsunami()

        if self.check_canceled():
            return  # キャンセルチェック
        self.create_hazard_area_landslide()

        if self.check_canceled():
            return  # キャンセルチェック
        self.create_hazard_area_floodplain()

    def create_station_coverage_area(self):
        """鉄道駅カバー圏域作成"""
        try:
            # railway_stations レイヤを取得
            railway_layer = self.gpkg_manager.load_layer(
                'railway_stations', None, withload_project=False
            )

            if not railway_layer.isValid():
                layer_name = self.tr("railway_stations")
                raise Exception(
                    self.tr(
                        "The %1 layer is invalid."
                    ).replace("%1", layer_name)
                )

            # バッファの距離
            buffer_distance = self.threshold_railway  # 閾値（単位: m）

            # 投影座標系に変換（EPSG:3857 - Web Mercator）
            target_crs = QgsCoordinateReferenceSystem('EPSG:3857')

            # 座標系変換
            reprojected_layer = processing.run(
                "native:reprojectlayer",
                {
                    'INPUT': railway_layer,
                    'TARGET_CRS': target_crs,
                    'OUTPUT': 'memory:',
                },
            )['OUTPUT']

            # プロセシングでバッファを作成（属性を保持）
            buffer_layer = processing.run(
                "native:buffer",
                {
                    'INPUT': reprojected_layer,
                    'DISTANCE': buffer_distance,
                    'SEGMENTS': 5,
                    'END_CAP_STYLE': 0,  # Round
                    'JOIN_STYLE': 0,  # Round
                    'MITER_LIMIT': 2,
                    'DISSOLVE': False,  # 個々のフィーチャを保持
                    'OUTPUT': 'memory:',
                },
            )['OUTPUT']

            # buffer_distance フィールドを追加
            buffer_provider = buffer_layer.dataProvider()
            buffer_provider.addAttributes(
                [QgsField("buffer_distance", QVariant.Double)]
            )
            buffer_layer.updateFields()

            # buffer_distance の値を設定
            buffer_layer.startEditing()
            for feature in buffer_layer.getFeatures():
                buffer_layer.changeAttributeValue(
                    feature.id(),
                    buffer_layer.fields().indexOf("buffer_distance"),
                    float(buffer_distance)
                )
            buffer_layer.commitChanges()

            # GeoPackage に保存
            if not self.gpkg_manager.add_layer(
                buffer_layer, "railway_station_buffers", "鉄道駅カバー圏域"
            ):
                raise Exception(self.tr("Failed to add layer to GeoPackage."))

            data_name = self.tr("railway station buffer")
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

    def create_bus_stop_coverage_area(self):
        """バス停カバー圏域作成"""
        try:
            # bus_stops レイヤを取得
            bus_layer = self.gpkg_manager.load_layer(
                'bus_stops', None, withload_project=False
            )

            if not bus_layer.isValid():
                layer_name = self.tr("bus_stops")
                raise Exception(
                    self.tr(
                        "The %1 layer is invalid."
                    ).replace("%1", layer_name)
                )

            # バッファの距離
            buffer_distance = self.threshold_bus  # 閾値（単位: m）

            # 投影座標系に変換（EPSG:3857 - Web Mercator）
            target_crs = QgsCoordinateReferenceSystem('EPSG:3857')

            # 座標系変換
            reprojected_layer = processing.run(
                "native:reprojectlayer",
                {
                    'INPUT': bus_layer,
                    'TARGET_CRS': target_crs,
                    'OUTPUT': 'memory:',
                },
            )['OUTPUT']

            # プロセシングでバッファを作成（属性を保持）
            buffer_layer = processing.run(
                "native:buffer",
                {
                    'INPUT': reprojected_layer,
                    'DISTANCE': buffer_distance,
                    'SEGMENTS': 5,
                    'END_CAP_STYLE': 0,  # Round
                    'JOIN_STYLE': 0,  # Round
                    'MITER_LIMIT': 2,
                    'DISSOLVE': False,  # 個々のフィーチャを保持
                    'OUTPUT': 'memory:',
                },
            )['OUTPUT']

            # buffer_distance フィールドを追加
            buffer_provider = buffer_layer.dataProvider()
            buffer_provider.addAttributes(
                [QgsField("buffer_distance", QVariant.Double)]
            )
            buffer_layer.updateFields()

            # buffer_distance の値を設定
            buffer_layer.startEditing()
            for feature in buffer_layer.getFeatures():
                buffer_layer.changeAttributeValue(
                    feature.id(),
                    buffer_layer.fields().indexOf("buffer_distance"),
                    float(buffer_distance)
                )
            buffer_layer.commitChanges()

            # GeoPackage に保存
            if not self.gpkg_manager.add_layer(
                buffer_layer, "bus_stop_buffers", "バス停カバー圏域"
            ):
                raise Exception(self.tr("Failed to add layer to GeoPackage."))

            data_name = self.tr("bus stop buffer")
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

    def create_shelter(self):
        """避難施設作成"""
        try:
            # base_path 配下の「09_避難所」フォルダを再帰的に探索してShapefileを収集
            induction_area_folder = os.path.join(self.base_path, "09_避難所")
            shp_files = self.__get_shapefiles(induction_area_folder)

            # レイヤを格納するリスト
            layers = []

            for shp_file in shp_files:
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
                    "P20_001",
                    "P20_002",
                    "P20_003",
                    "P20_004",
                    "P20_005",
                    "P20_006",
                    "P20_007",
                    "P20_008",
                }

                if not required_fields.issubset(layer_fields):
                    data_name = self.tr("shelter")
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
                    f"Point?crs={layer.crs().authid()}", "shelters", "memory"
                )
                temp_provider = temp_layer.dataProvider()

                # 必要なフィールドを追加
                temp_provider.addAttributes(
                    [
                        QgsField("code", QVariant.String),
                        QgsField("name", QVariant.String),
                        QgsField("address", QVariant.String),
                        QgsField("type", QVariant.String),
                        QgsField("capacity", QVariant.Int),
                        QgsField("scale", QVariant.Int),
                        QgsField("earthquake", QVariant.Int),
                        QgsField("tunami", QVariant.Int),
                    ]
                )
                temp_layer.updateFields()

                # フィーチャの追加
                for feature in layer.getFeatures():
                    new_feature = QgsFeature()
                    new_feature.setGeometry(feature.geometry())

                    # 属性データのマッピング
                    attributes = [
                        feature["P20_001"],  # code
                        feature["P20_002"],  # name
                        feature["P20_003"],  # address
                        feature["P20_004"],  # type
                        feature["P20_005"],  # capacity
                        feature["P20_006"],  # scale
                        feature["P20_007"],  # earthquake
                        feature["P20_008"],  # tunami
                    ]
                    new_feature.setAttributes(attributes)
                    temp_provider.addFeature(new_feature)

                layers.append(temp_layer)

            if not layers:
                data_name = self.tr("shelter")
                msg = (
                    self.tr("No valid %1 Shapefile was found.")
                    .replace("%1", data_name)
                )
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Info,
                )

                # 一時メモリレイヤを作成
                temp_layer = QgsVectorLayer(
                    "Polygon", "land_prices", "memory")
                temp_provider = temp_layer.dataProvider()

                # 必要なフィールドを追加
                temp_provider.addAttributes(
                    [
                        QgsField("rank", QVariant.String),
                    ]
                )
                temp_layer.updateFields()
                layers.append(temp_layer)

            # 複数のレイヤをマージ
            merged_layer = self.__merge_layers(layers)

            # sheltersレイヤをGeoPackageに保存
            if not self.gpkg_manager.add_layer(
                merged_layer, "shelters", "避難施設"
            ):
                raise Exception(self.tr("Failed to add layer to GeoPackage."))

            data_name = self.tr("shelter")
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

    def create_shelter_area(self):
        """避難施設圏域作成"""
        try:
            # 既存のsheltersレイヤをレイヤパネルから取得
            shelters_layer = self.gpkg_manager.load_layer(
                'shelters', None, withload_project=False
            )
            if not shelters_layer:
                raise Exception(self.tr("The %1 layer was not found.")
                    .replace("%1", "shelters"))

            # 道路ネットワークデータ（ロードネットワークレイヤ）をレイヤパネルから取得
            road_network_layer = self.gpkg_manager.load_layer(
                'road_networks', None, withload_project=False
            )
            if not road_network_layer:
                raise Exception(self.tr("The %1 layer was not found.")
                    .replace("%1", "road_networks"))

            distance = self.threshold_shelter

            # 一時メモリレイヤの作成 (Polygonタイプ)
            tmp_buffer_layer = QgsVectorLayer(
                "Polygon?crs=EPSG:3857", "tmp_shelter_area", "memory"
            )
            temp_buffer_provider = tmp_buffer_layer.dataProvider()
            # 必要なフィールドを追加
            temp_buffer_provider.addAttributes(
                [QgsField("shelter_id", QVariant.String)]
            )
            tmp_buffer_layer.updateFields()

            # shelter_buffersレイヤを作成
            shelter_buffer_layer = QgsVectorLayer(
                "Polygon?crs=EPSG:3857", "shelter_buffers", "memory"
            )
            shelter_buffer_provider = shelter_buffer_layer.dataProvider()
            shelter_buffer_provider.addAttributes(
                [QgsField("shelter_id", QVariant.String)]
            )
            shelter_buffer_layer.updateFields()

            target_crs = QgsCoordinateReferenceSystem("EPSG:3857")

            # 避難所を投影座標系へ変換
            shelters_layer = processing.run(
                "native:reprojectlayer",
                {
                    'INPUT': shelters_layer,
                    'TARGET_CRS': target_crs,  # オブジェクトを使用
                    'OUTPUT': 'memory:',  # 一時メモリレイヤとして出力
                },
            )['OUTPUT']

            if self.check_canceled():
                return  # キャンセルチェック

            # 道路ネットワークを投影座標系へ変換
            road_network_layer = processing.run(
                "native:reprojectlayer",
                {
                    'INPUT': road_network_layer,
                    'TARGET_CRS': target_crs,  # オブジェクトを使用
                    'OUTPUT': 'memory:',  # 一時メモリレイヤとして出力
                },
            )['OUTPUT']

            if self.check_canceled():
                return  # キャンセルチェック

            shelter_count = 0  # 処理した避難所のカウント用変数
            # 各避難施設のフィーチャに対して徒歩圏バッファを作成
            for shelter_feature in shelters_layer.getFeatures():
                shelter_geometry = shelter_feature.geometry()

                # 一時的にバッファを作成
                buffer = shelter_geometry.buffer(distance, segments=8)

                # バッファ内の道路を取得
                request = QgsFeatureRequest().setFilterRect(
                    buffer.boundingBox()
                )
                nearby_roads = road_network_layer.getFeatures(request)
                nearby_roads = list(nearby_roads)

                # 道路ネットワークのノード情報を取得
                crs = road_network_layer.crs()

                node_graph = self.__extract_road_nodes(nearby_roads, crs)

                point_geom = shelter_feature.geometry()
                if point_geom.type() == QgsWkbTypes.PointGeometry:
                    point = shelter_feature.geometry().asPoint()
                else:
                    msg = self.tr(
                        "Shelter geometry is not a Point: %1"
                    ).replace("%1", point_geom.asWkt())
                    QgsMessageLog.logMessage(
                        msg,
                        self.tr("Plugin"),
                        Qgis.Warning,
                    )

                # 避難所の属性 'scale' に基づいて経路開始地点(k)の数を決定
                shelter_scale = shelter_feature['scale']  # 避難所の規模を取得
                k = (
                    1 if shelter_scale == -1 else 3
                )  # 'scale'（施設規模） が -1 なら k=1、それ以外なら k=3 とする

                # 避難所の座標に最も近い道路ノードを取得
                nearest_nodes = self.nearest_point(
                    node_graph, point, k=k
                )  # k に基づいて調整

                # ダイクストラ法を実行し、バッファ範囲を計算
                buffer_distance = 200  # 道路に対して200mのバッファサイズ
                merge_searched_road = self.dijkstra(
                    node_graph, nearest_nodes, distance, [], buffer_distance
                )

                if self.check_canceled():
                    break  # キャンセルチェック

                # ダイクストラで計算されたバッファポリゴンをレイヤに追加
                for buffered_polygon in merge_searched_road:
                    shelter_buffer_feature = QgsFeature()

                    # バッファポリゴンをセット
                    if isinstance(
                        buffered_polygon, Polygon
                    ):  # ポリゴンかどうかをチェック
                        shelter_buffer_feature.setGeometry(
                            QgsGeometry.fromWkt(buffered_polygon.wkt)
                        )
                        shelter_buffer_feature.setAttributes(
                            [str(shelter_feature["fid"])]
                        )
                        shelter_buffer_provider.addFeature(
                            shelter_buffer_feature
                        )
                        shelter_buffer_layer.updateExtents()  # レイヤの範囲を更新

                shelter_count += 1
                if (shelter_count % 100) == 0:
                    QApplication.processEvents()

            # shelter_buffersレイヤをGeoPackageに保存
            if not self.gpkg_manager.add_layer(
                shelter_buffer_layer, "shelter_buffers", "避難施設カバー圏域"
            ):
                raise Exception(self.tr("Failed to add layer to GeoPackage."))

            data_name = self.tr("shelter buffer")
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

    def __extract_road_nodes(self, roads, crs):
        """道路ネットワークレイヤからノード情報を抽出し、グラフを構築するメソッド"""

        # グラフビルダーの設定（CRSは道路レイヤと同じ）
        builder = QgsGraphBuilder(crs)

        total_count = 0
        linestring_count = 0
        vertex_map = {}  # 頂点IDを管理するマップ
        vertex_id_counter = 0  # 頂点IDカウンター

        # 道路フィーチャを処理
        for road_feature in roads:
            total_count += 1
            road_geom = road_feature.geometry()

            if road_geom.isEmpty():
                QgsMessageLog.logMessage(
                    self.tr("Empty geometry for road feature."),
                    self.tr("Plugin"),
                    Qgis.Warning,
                )
                continue

            # ジオメトリのタイプを確認
            geom_type = road_geom.wkbType()

            if geom_type in (
                QgsWkbTypes.LineString,
                QgsWkbTypes.MultiLineString,
            ):
                if geom_type == QgsWkbTypes.MultiLineString:
                    lines = road_geom.asMultiPolyline()
                else:
                    lines = [road_geom.asPolyline()]

                # 各ラインの処理
                for line in lines:
                    if len(line) < 2:
                        continue  # ラインが2点未満の場合はスキップ

                    for i in range(len(line) - 1):
                        p1 = QgsPointXY(line[i])
                        p2 = QgsPointXY(line[i + 1])

                        # p1, p2のIDを生成、または既存のIDを取得
                        if p1 not in vertex_map:
                            vertex_map[p1] = vertex_id_counter
                            builder.addVertex(vertex_id_counter, p1)
                            vertex_id_counter += 1
                        if p2 not in vertex_map:
                            vertex_map[p2] = vertex_id_counter
                            builder.addVertex(vertex_id_counter, p2)
                            vertex_id_counter += 1

                        id1 = vertex_map[p1]
                        id2 = vertex_map[p2]

                        # ノード間のエッジを追加
                        builder.addEdge(
                            id1,
                            p1,
                            id2,
                            p2,
                            [QgsGeometry.fromPolylineXY([p1, p2]).length()],
                        )

                        linestring_count += 1
            else:
                msg = self.tr(
                    "Unsupported geometry type: %1"
                ).replace("%1", geom_type)
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Warning,
                )
                continue

        # グラフを作成
        graph = builder.graph()

        return graph  # ノードとエッジが含まれたグラフを返す

    def calculate_meter(self, starting_point, destination):
        """
        starting_point,からdestinationまでの距離を測る
        """
        point_geom = QgsGeometry.fromPointXY(
            QgsPointXY(starting_point[0], starting_point[1])
        )
        dest_geom = QgsGeometry.fromPointXY(
            QgsPointXY(destination[0], destination[1])
        )

        # メートル単位の距離
        distance = point_geom.distance(dest_geom)
        return distance

    def dijkstra(
        self, graph, start_points, max_distance, shelters_inf, buffer_distance
    ):
        """
        ダイクストラ法を使用した開始位置から各ノードへの最短距離の探索
        """
        searched_list = set()
        searched_roads = []
        merge_searched_road = []

        priority_queue = []

        for start in start_points:
            priority_queue.append((0, (start.x(), start.y())))

        heapq.heapify(priority_queue)

        while priority_queue:
            # 最小の値を持つノードを取得
            current_distance, current_node = heapq.heappop(priority_queue)

            # ノードを探索済みリストに追加
            if current_node in searched_list:
                continue
            searched_list.add(current_node)

            # 最大距離を超えた場合の処理
            if current_distance > max_distance:
                continue

            # ノードIDを取得
            vertex_id = graph.findVertex(
                QgsPointXY(current_node[0], current_node[1])
            )
            if vertex_id == -1:
                # ノードが見つからない場合はスキップ
                continue

            # エッジの探索
            for edge_idx in (
                graph.vertex(vertex_id).outgoingEdges()
                + graph.vertex(vertex_id).incomingEdges()
            ):
                edge = graph.edge(edge_idx)
                start_node = graph.vertex(edge.fromVertex()).point()
                end_node = graph.vertex(edge.toVertex()).point()

                # エッジのコストを取得
                length = edge.cost(0)

                next_node = None
                if (current_node[0], current_node[1]) == (
                    start_node.x(),
                    start_node.y(),
                ):
                    next_node = end_node
                elif (current_node[0], current_node[1]) == (
                    end_node.x(),
                    end_node.y(),
                ):
                    next_node = start_node
                else:
                    continue

                # 距離の計算
                new_distance = current_distance + length

                if (
                    next_node not in searched_list
                    and new_distance <= max_distance
                ):
                    heapq.heappush(
                        priority_queue,
                        (new_distance, (next_node.x(), next_node.y())),
                    )

                    # バッファの計算
                    if new_distance <= max_distance:
                        buff = (
                            buffer_distance
                            * (max_distance - new_distance)
                            / max_distance
                        )
                        line = LineString(
                            [
                                QgsPointXY(start_node.x(), start_node.y()),
                                QgsPointXY(end_node.x(), end_node.y()),
                            ]
                        )
                        buffered_line = line.buffer(buff)

                        searched_roads.append(buffered_line)

        # 全ての道路ポリゴンを結合して返す
        merge_searched_road.append(unary_union(searched_roads))
        return merge_searched_road

    def nearest_point(self, node_graph, point, k):
        """
        指定座標からk番目までに近いノードを見つける
        node_graph   : QgsGraph
            ノードデータを持つグラフ
        point        : QgsPointXY
            指定座標
        k            : int
            何番目まで近い点を見つけるか

        return
        nearest_points   : list
            k番目までの近いノード座標
        """
        # 初期値の設定
        nearest = []
        nearest_points = []
        for i in range(k):
            nearest.append(float('inf'))  # 非常に大きい値で初期化
            nearest_points.append(point)

        # ノードリストを取得
        for vertex_id in range(node_graph.vertexCount()):
            node_point = node_graph.vertex(vertex_id).point()
            distance = self.calculate_meter(
                (point.x(), point.y()), (node_point.x(), node_point.y())
            )

            # 一番近い値をk個まで保持
            if distance < max(nearest):
                max_idx = nearest.index(max(nearest))  # 最も遠い現在の値を更新
                nearest[max_idx] = distance
                nearest_points[max_idx] = node_point

        return nearest_points

    def create_urban_function_induction_area(self):
        """都市機能誘導区域/居住誘導区域 作成"""
        try:
            # base_path 配下の「21_誘導区域」フォルダを再帰的に探索してShapefileを収集
            induction_area_folder = os.path.join(self.base_path, "21_誘導区域")
            shp_files = self.__get_shapefiles(induction_area_folder)

            # レイヤを格納するリスト
            layers = []

            if not shp_files:
                data_name = self.tr("induction area")
                msg = (
                    self.tr("No Shapefile found for the %1. Creating empty layer.")
                    .replace("%1", data_name)
                )
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Info,
                )

                # 空のレイヤを作成
                empty_layer = QgsVectorLayer(
                    "Polygon", "induction_areas", "memory"
                )
                empty_provider = empty_layer.dataProvider()
                # 必要なフィールドを追加
                empty_provider.addAttributes(
                    [
                        QgsField("type", QVariant.String),
                        QgsField("type_id", QVariant.Int),
                        QgsField("prefecture_name", QVariant.String),
                        QgsField("city_code", QVariant.String),
                        QgsField("city_name", QVariant.String),
                        QgsField("first_decision_date", QVariant.String),
                        QgsField("last_decision_date", QVariant.String),
                        QgsField("decision_type", QVariant.Int),
                        QgsField("decider", QVariant.String),
                        QgsField("notice_number_s", QVariant.String),
                        QgsField("notice_number_l", QVariant.String),
                    ]
                )
                empty_layer.updateFields()

                # 空のレイヤをGeoPackageに保存（レイヤパネルには追加しない）
                if not self.gpkg_manager.add_layer(
                    empty_layer, "induction_areas", "誘導区域",
                    withload_project=False
                ):
                    raise Exception(self.tr("Failed to add layer to GeoPackage."))

                return True

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
                    "AreaType",
                    "AreaCode",
                    "Pref",
                    "Citycode",
                    "Cityname",
                    "INDate",
                    "FNDate",
                    "ValidType",
                    "Custodian",
                    "INNumber",
                    "FNNumber",
                }

                if not required_fields.issubset(layer_fields):
                    data_name = self.tr("induction area")
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
                    f"Polygon?crs={layer.crs().authid()}",
                    "induction_areas",
                    "memory",
                )
                temp_provider = temp_layer.dataProvider()

                # 必要なフィールドを追加
                temp_provider.addAttributes(
                    [
                        QgsField("type", QVariant.String),
                        QgsField("type_id", QVariant.Int),
                        QgsField("prefecture_name", QVariant.String),
                        QgsField("city_code", QVariant.String),
                        QgsField("city_name", QVariant.String),
                        QgsField("first_decision_date", QVariant.String),
                        QgsField("last_decision_date", QVariant.String),
                        QgsField("decision_type", QVariant.Int),
                        QgsField("decider", QVariant.String),
                        QgsField("notice_number_s", QVariant.String),
                        QgsField("notice_number_l", QVariant.String),
                    ]
                )
                temp_layer.updateFields()

                # フィーチャの追加
                for feature in layer.getFeatures():
                    if self.check_canceled():
                        return  # キャンセルチェック
                    new_feature = QgsFeature()
                    new_feature.setGeometry(feature.geometry())

                    # 属性データのマッピング
                    attributes = [
                        feature["AreaType"],  # type
                        feature["AreaCode"],  # type_id
                        feature["Pref"],  # prefecture_name
                        feature["Citycode"],  # city_code
                        feature["Cityname"],  # city_name
                        feature["INDate"],  # first_decision_date
                        feature["FNDate"],  # last_decision_date
                        feature["ValidType"],  # decision_type
                        feature["Custodian"],  # decider
                        feature["INNumber"],  # notice_number_s
                        feature["FNNumber"],  # notice_number_l
                    ]
                    new_feature.setAttributes(attributes)
                    temp_provider.addFeature(new_feature)

                layers.append(temp_layer)

            if not layers:
                # Shapefileは見つかったが有効なデータがなかった場合、空のレイヤを作成
                data_name = self.tr("induction area")
                msg = (
                    self.tr("No valid Shapefile found for the %1. Creating empty layer.")
                    .replace("%1", data_name)
                )
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Info,
                )

                # 空のレイヤを作成
                empty_layer = QgsVectorLayer(
                    "Polygon", "induction_areas", "memory"
                )
                empty_provider = empty_layer.dataProvider()
                # 必要なフィールドを追加
                empty_provider.addAttributes(
                    [
                        QgsField("type", QVariant.String),
                        QgsField("type_id", QVariant.Int),
                        QgsField("prefecture_name", QVariant.String),
                        QgsField("city_code", QVariant.String),
                        QgsField("city_name", QVariant.String),
                        QgsField("first_decision_date", QVariant.String),
                        QgsField("last_decision_date", QVariant.String),
                        QgsField("decision_type", QVariant.Int),
                        QgsField("decider", QVariant.String),
                        QgsField("notice_number_s", QVariant.String),
                        QgsField("notice_number_l", QVariant.String),
                    ]
                )
                empty_layer.updateFields()

                # 空のレイヤをGeoPackageに保存（レイヤパネルには追加しない）
                if not self.gpkg_manager.add_layer(
                    empty_layer, "induction_areas", "誘導区域",
                    withload_project=False
                ):
                    raise Exception(self.tr("Failed to add layer to GeoPackage."))

                return True

            # 複数のレイヤをマージ
            merged_layer = self.__merge_layers(layers)

            # type_id=31(居住誘導区域)またはtype_id=32(都市機能誘導区域)が存在するかチェック
            has_valid_induction_areas = False
            for feature in merged_layer.getFeatures():
                type_id = feature["type_id"]
                if type_id == 31 or type_id == 32:
                    has_valid_induction_areas = True
                    break

            # induction_areasレイヤをGeoPackageに保存
            # type_id=31,32がない場合はレイヤパネルに追加しない
            if not self.gpkg_manager.add_layer(
                merged_layer, "induction_areas", "誘導区域",
                withload_project=has_valid_induction_areas
            ):
                raise Exception(self.tr("Failed to add layer to GeoPackage."))

            data_name = self.tr("induction area")
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

    def create_hypothetical_residential_areas(self):
        """仮想居住誘導区域 作成"""
        try:
            # 誘導区域に居住誘導区域(type_id=31)が存在するかチェック
            induction_areas_layer = self.gpkg_manager.load_layer(
                'induction_areas', None, withload_project=False
            )

            has_residential_induction = False
            if induction_areas_layer:
                for feature in induction_areas_layer.getFeatures():
                    if feature.attribute('type_id') == 31:
                        has_residential_induction = True
                        break

            # base_path 配下の「22_仮想居住誘導区域」フォルダを再帰的に探索してShapefileを収集
            hypothetical_folder = os.path.join(self.base_path, "22_仮想居住誘導区域")
            shp_files = self.__get_shapefiles(hypothetical_folder)

            # レイヤを格納するリスト
            layers = []

            if not shp_files:
                data_name = self.tr("hypothetical residential areas")
                msg = (
                    self.tr("No Shapefile found for the %1. Creating empty layer.")
                    .replace("%1", data_name)
                )
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Info,
                )

                # 空のレイヤを作成
                empty_layer = QgsVectorLayer(
                    "Polygon", "hypothetical_residential_areas", "memory"
                )
                empty_provider = empty_layer.dataProvider()
                # 最小限のフィールドを追加
                empty_provider.addAttributes(
                    [QgsField("id", QVariant.Int), QgsField("type_id", QVariant.Int)]
                )
                empty_layer.updateFields()

                # 居住誘導区域がある場合はレイヤパネルに追加しない
                # 居住誘導区域がない場合はレイヤパネルに追加する
                if not self.gpkg_manager.add_layer(
                    empty_layer, "hypothetical_residential_areas", "仮想居住誘導区域",
                    withload_project=not has_residential_induction
                ):
                    raise Exception(self.tr("Failed to add layer to GeoPackage."))

                return True

            for shp_file in shp_files:
                if self.check_canceled():
                    return  # キャンセルチェック
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

                # 一時メモリレイヤを作成し、Shapefileのデータをそのまま取り込み
                temp_layer = QgsVectorLayer(
                    f"Polygon?crs={layer.crs().authid()}",
                    "hypothetical_residential_areas",
                    "memory",
                )
                temp_provider = temp_layer.dataProvider()

                # 元のShapefileの全フィールドをそのまま追加
                fields_to_add = []
                for field in layer.fields():
                    fields_to_add.append(QgsField(field.name(), field.type()))
                temp_provider.addAttributes(fields_to_add)
                temp_layer.updateFields()

                # フィーチャをそのまま追加
                for feature in layer.getFeatures():
                    if self.check_canceled():
                        return  # キャンセルチェック
                    new_feature = QgsFeature()
                    new_feature.setGeometry(feature.geometry())
                    # 全属性をそのままコピー
                    new_feature.setAttributes(feature.attributes())
                    temp_provider.addFeature(new_feature)

                layers.append(temp_layer)

            if not layers:
                raise Exception(
                    "有効な仮想居住誘導区域のShapefileが見つかりませんでした。"
                )

            # 複数のレイヤをマージ
            merged_layer = self.__merge_layers(layers)

            # type_idフィールドが存在しない場合は追加
            provider = merged_layer.dataProvider()
            if 'type_id' not in merged_layer.fields().names():
                provider.addAttributes([QgsField('type_id', QVariant.Int)])
                merged_layer.updateFields()

            # すべてのフィーチャのtype_idを31（居住誘導区域）に設定
            field_idx = merged_layer.fields().indexOf('type_id')
            updates = {}
            for feature in merged_layer.getFeatures():
                updates[feature.id()] = {field_idx: 31}

            if updates:
                provider.changeAttributeValues(updates)

            # 居住誘導区域がある場合はレイヤパネルに追加しない
            # 居住誘導区域がない場合はレイヤパネルに追加する
            if not self.gpkg_manager.add_layer(
                merged_layer, "hypothetical_residential_areas", "仮想居住誘導区域",
                withload_project=not has_residential_induction
            ):
                raise Exception(self.tr("Failed to add layer to GeoPackage."))

            data_name = self.tr("hypothetical residential areas")
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

    def validate_induction_areas(self):
        """誘導区域と仮想居住誘導区域の両方が空でないかチェック"""
        try:
            # 誘導区域レイヤを読み込み
            induction_areas_layer = self.gpkg_manager.load_layer(
                'induction_areas', None, withload_project=False
            )

            # 仮想居住誘導区域レイヤを読み込み
            hypothetical_residential_areas_layer = self.gpkg_manager.load_layer(
                'hypothetical_residential_areas', None, withload_project=False
            )

            # 誘導区域レイヤに居住誘導区域(type_id=31)が存在するかチェック
            has_residential_induction = False
            if induction_areas_layer:
                for feature in induction_areas_layer.getFeatures():
                    if feature["type_id"] == 31:
                        has_residential_induction = True
                        break

            # 仮想居住誘導区域レイヤのフィーチャ数をチェック
            hypothetical_count = 0
            if hypothetical_residential_areas_layer:
                hypothetical_count = hypothetical_residential_areas_layer.featureCount()

            # 両方とも存在しない場合はエラー
            if not has_residential_induction and hypothetical_count == 0:
                msg = "誘導区域データがありません（居住誘導区域または仮想居住誘導区域が必要です）"
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Critical,
                )
                raise Exception(msg)

            return True

        except Exception as e:
            QgsMessageLog.logMessage(
                self.tr("An error occurred: %1").replace("%1", str(e)),
                self.tr("Plugin"),
                Qgis.Critical,
            )
            raise e

    def create_land_use_maps(self):
        """土地利用細分化メッシュ 作成"""
        try:
            # base_path 配下の「14_土地利用細分化メッシュ」フォルダを再帰的に探索してShapefileを収集
            land_use_folder = os.path.join(self.base_path, "14_土地利用細分化メッシュ")
            shp_files = self.__get_shapefiles(land_use_folder)

            # レイヤを格納するリスト
            layers = []

            if not shp_files:
                data_name = self.tr("land use maps")
                msg = (
                    self.tr("No Shapefile found for the %1. Creating empty layer.")
                    .replace("%1", data_name)
                )
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Info,
                )

                # 空のレイヤを作成
                empty_layer = QgsVectorLayer(
                    "Polygon", "land_use_maps", "memory"
                )
                empty_provider = empty_layer.dataProvider()
                # フィールドを追加
                empty_provider.addAttributes(
                    [
                        QgsField("code", QVariant.String),
                        QgsField("type", QVariant.String),
                        QgsField("snap_date", QVariant.String),
                    ]
                )
                empty_layer.updateFields()

                # 空のレイヤをGeoPackageに保存
                if not self.gpkg_manager.add_layer(
                    empty_layer, "land_use_maps", "土地利用細分化メッシュ"
                ):
                    raise Exception(self.tr("Failed to add layer to GeoPackage."))

                return True

            for shp_file in shp_files:
                if self.check_canceled():
                    return  # キャンセルチェック
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
                    "L03b_001",
                    "L03b_002",
                    "L03b_003",
                }

                if not required_fields.issubset(layer_fields):
                    data_name = self.tr("land use maps")
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
                    f"Polygon?crs={layer.crs().authid()}",
                    "land_use_maps",
                    "memory",
                )
                temp_provider = temp_layer.dataProvider()

                # 必要なフィールドを追加
                temp_provider.addAttributes(
                    [
                        QgsField("code", QVariant.String),
                        QgsField("type", QVariant.String),
                        QgsField("snap_date", QVariant.String),
                    ]
                )
                temp_layer.updateFields()

                # フィーチャの追加
                for feature in layer.getFeatures():
                    if self.check_canceled():
                        return  # キャンセルチェック
                    new_feature = QgsFeature()
                    new_feature.setGeometry(feature.geometry())

                    # 属性データのマッピング
                    attributes = [
                        feature["L03b_001"],  # code
                        feature["L03b_002"],  # type
                        feature["L03b_003"],  # snap_date
                    ]
                    new_feature.setAttributes(attributes)
                    temp_provider.addFeature(new_feature)

                layers.append(temp_layer)

            if not layers:
                raise Exception(
                    "有効な土地利用細分化メッシュのShapefileが見つかりませんでした。"
                )

            # 複数のレイヤをマージ
            merged_layer = self.__merge_layers(layers)

            # land_use_mapsレイヤをGeoPackageに保存
            if not self.gpkg_manager.add_layer(
                merged_layer, "land_use_maps", "土地利用細分化メッシュ"
            ):
                raise Exception(self.tr("Failed to add layer to GeoPackage."))

            data_name = self.tr("land use maps")
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

    def create_change_maps(self):
        """変化度マップ 作成"""
        try:
            # base_path 配下の「13_変化度マップ（建物変化）」フォルダを再帰的に探索してShapefileを収集
            change_maps_folder = os.path.join(self.base_path, "13_変化度マップ（建物変化）")
            all_shp_files = self.__get_shapefiles(change_maps_folder)

            # 建物変化新築のファイルのみをフィルタリング
            shp_files = [
                shp_file for shp_file in all_shp_files
                if "変化度マップ_建物変化-新築" in os.path.basename(shp_file)
            ]

            # レイヤを格納するリスト
            layers = []

            if not shp_files:
                data_name = self.tr("change maps")
                msg = (
                    self.tr("No building change Shapefile found for the %1. Creating empty layer.")
                    .replace("%1", data_name)
                )
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Info,
                )

                # 空のレイヤを作成
                empty_layer = QgsVectorLayer(
                    "Polygon", "change_maps", "memory"
                )
                empty_provider = empty_layer.dataProvider()
                # フィールドを追加
                empty_provider.addAttributes(
                    [
                        QgsField("code", QVariant.String),
                        QgsField("type", QVariant.String),
                        QgsField("level", QVariant.Int),
                        QgsField("old_date", QVariant.String),
                        QgsField("new_date", QVariant.String),
                    ]
                )
                empty_layer.updateFields()

                # 空のレイヤをGeoPackageに保存
                if not self.gpkg_manager.add_layer(
                    empty_layer, "change_maps", "変化度マップ"
                ):
                    raise Exception(self.tr("Failed to add layer to GeoPackage."))

                return True

            for shp_file in shp_files:
                if self.check_canceled():
                    return  # キャンセルチェック
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
                    "コード",
                    "変化種別",
                    "変化度",
                    "旧撮影日",
                    "新撮影日",
                }

                if not required_fields.issubset(layer_fields):
                    missing_fields = required_fields - layer_fields
                    available_fields = layer_fields

                    data_name = self.tr("change maps")
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

                    # 不足しているフィールドをログ出力
                    missing_msg = self.tr(
                        "Missing required fields: %1"
                    ).replace("%1", str(missing_fields))
                    QgsMessageLog.logMessage(
                        missing_msg,
                        self.tr("Plugin"),
                        Qgis.Warning,
                    )

                    # 利用可能なフィールドをログ出力
                    available_msg = self.tr(
                        "Available fields in shapefile: %1"
                    ).replace("%1", str(list(available_fields)))
                    QgsMessageLog.logMessage(
                        available_msg,
                        self.tr("Plugin"),
                        Qgis.Info,
                    )
                    continue

                # 一時メモリレイヤを作成し、Shapefileのデータを取り込み
                temp_layer = QgsVectorLayer(
                    f"Polygon?crs={layer.crs().authid()}",
                    "change_maps",
                    "memory",
                )
                temp_provider = temp_layer.dataProvider()

                # 必要なフィールドを追加
                temp_provider.addAttributes(
                    [
                        QgsField("code", QVariant.String),
                        QgsField("type", QVariant.String),
                        QgsField("level", QVariant.Int),
                        QgsField("old_date", QVariant.String),
                        QgsField("new_date", QVariant.String),
                    ]
                )
                temp_layer.updateFields()

                # フィーチャの追加
                for feature in layer.getFeatures():
                    if self.check_canceled():
                        return  # キャンセルチェック
                    new_feature = QgsFeature()
                    new_feature.setGeometry(feature.geometry())

                    # 属性データのマッピング
                    attributes = [
                        feature["コード"],      # code
                        feature["変化種別"],    # type
                        feature["変化度"],      # level
                        feature["旧撮影日"],    # old_date
                        feature["新撮影日"],    # new_date
                    ]
                    new_feature.setAttributes(attributes)
                    temp_provider.addFeature(new_feature)

                layers.append(temp_layer)

            if not layers:
                # フィルタリング後にファイルがない場合も空のレイヤを作成
                empty_layer = QgsVectorLayer(
                    "Polygon", "change_maps", "memory"
                )
                empty_provider = empty_layer.dataProvider()
                empty_provider.addAttributes(
                    [
                        QgsField("code", QVariant.String),
                        QgsField("type", QVariant.String),
                        QgsField("level", QVariant.String),
                        QgsField("old_date", QVariant.String),
                        QgsField("new_date", QVariant.String),
                    ]
                )
                empty_layer.updateFields()

                if not self.gpkg_manager.add_layer(
                    empty_layer, "change_maps", "変化度マップ"
                ):
                    raise Exception(self.tr("Failed to add layer to GeoPackage."))

                return True

            # 複数のレイヤをマージ
            merged_layer = self.__merge_layers(layers)

            # change_mapsレイヤをGeoPackageに保存
            if not self.gpkg_manager.add_layer(
                merged_layer, "change_maps", "変化度マップ"
            ):
                raise Exception(self.tr("Failed to add layer to GeoPackage."))

            data_name = self.tr("change maps")
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

    def create_urban_planning_area(self):
        """都市計画区域 作成"""
        try:
            # base_path 配下の「21_誘導区域」フォルダを再帰的に探索してShapefileを収集
            induction_area_folder = os.path.join(self.base_path, "21_誘導区域")
            shp_files = self.__get_shapefiles(induction_area_folder)

            if not shp_files:
                data_name = self.tr("induction area")
                msg = (
                    self.tr("No valid %1 Shapefile was found.")
                    .replace("%1", data_name)
                )
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Info,
                )
                raise Exception(msg)

            # レイヤを格納するリスト
            layers = []

            for shp_file in shp_files:
                if self.check_canceled():
                    return  # キャンセルチェック
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
                    "TokeiName",
                    "TokeiType",
                    "TokeiCode",
                    "Pref",
                    "Citycode",
                    "Cityname",
                    "INDate",
                    "FNDate",
                    "ValidType",
                    "Custodian",
                    "INNumber",
                    "FNNumber",
                }

                if not required_fields.issubset(layer_fields):
                    data_name = self.tr("urbun planning")
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
                    f"Polygon?crs={layer.crs().authid()}",
                    "urban_plannings",
                    "memory",
                )
                temp_provider = temp_layer.dataProvider()

                # 必要なフィールドを追加
                temp_provider.addAttributes(
                    [
                        QgsField("tokei_name", QVariant.String),
                        QgsField("type", QVariant.String),
                        QgsField("type_id", QVariant.Int),
                        QgsField("prefecture_name", QVariant.String),
                        QgsField("city_code", QVariant.String),
                        QgsField("city_name", QVariant.String),
                        QgsField("first_decision_date", QVariant.String),
                        QgsField("last_decision_date", QVariant.String),
                        QgsField("decision_type", QVariant.String),
                        QgsField("decider", QVariant.String),
                        QgsField("notice_number_s", QVariant.String),
                        QgsField("notice_number_l", QVariant.String),
                    ]
                )
                temp_layer.updateFields()

                # フィーチャの追加
                for feature in layer.getFeatures():
                    if self.check_canceled():
                        return  # キャンセルチェック
                    new_feature = QgsFeature()
                    new_feature.setGeometry(feature.geometry())

                    # 属性データのマッピング
                    attributes = [
                        feature["TokeiName"],  # tokei_name
                        feature["TokeiType"],  # type
                        feature["TokeiCode"],  # type_id
                        feature["Pref"],  # prefecture_name
                        feature["Citycode"],  # city_code
                        feature["Cityname"],  # city_name
                        feature["INDate"],  # first_decision_date
                        feature["FNDate"],  # last_decision_date
                        feature["ValidType"],  # decision_type
                        feature["Custodian"],  # decider
                        feature["INNumber"],  # notice_number_s
                        feature["FNNumber"],  # notice_number_l
                    ]
                    new_feature.setAttributes(attributes)
                    temp_provider.addFeature(new_feature)

                layers.append(temp_layer)

            if not layers:
                raise Exception(
                    "有効な都市計画区域のShapefileが見つかりませんでした。"
                )

            # 複数のレイヤをマージ
            merged_layer = self.__merge_layers(layers)

            # urban_planningsレイヤをGeoPackageに保存
            if not self.gpkg_manager.add_layer(
                merged_layer, "urban_plannings", "都市計画区域"
            ):
                raise Exception(self.tr("Failed to add layer to GeoPackage."))

            data_name = self.tr("urban_planning")
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

    def create_land_use_area(self):
        """用途地域 作成"""
        try:
            # base_path 配下の「21_誘導区域」フォルダを再帰的に探索してShapefileを収集
            induction_area_folder = os.path.join(self.base_path, "21_誘導区域")
            shp_files = self.__get_shapefiles(induction_area_folder)

            if not shp_files:
                data_name = self.tr("induction area")
                msg = (
                    self.tr("No valid %1 Shapefile was found.")
                    .replace("%1", data_name)
                )
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Info,
                )
                raise Exception(msg)

            # レイヤを格納するリスト
            layers = []

            for shp_file in shp_files:
                if self.check_canceled():
                    return  # キャンセルチェック
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
                    "YoutoName",
                    "YoutoCode",
                    "FAR",
                    "BCR",
                    "Pref",
                    "Citycode",
                    "Cityname",
                    "INDate",
                    "FNDate",
                    "ValidType",
                    "Custodian",
                    "INNumber",
                    "FNNumber",
                }

                if not required_fields.issubset(layer_fields):
                    data_name = self.tr("land use area")
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
                    f"Polygon?crs={layer.crs().authid()}",
                    "land_use_areas",
                    "memory",
                )
                temp_provider = temp_layer.dataProvider()

                # 必要なフィールドを追加
                temp_provider.addAttributes(
                    [
                        QgsField("type", QVariant.String),
                        QgsField("type_id", QVariant.Int),
                        QgsField("area_ratio", QVariant.String),
                        QgsField("bulding_coverage_ratio", QVariant.String),
                        QgsField("prefecture_name", QVariant.String),
                        QgsField("city_code", QVariant.String),
                        QgsField("city_name", QVariant.String),
                        QgsField("first_decision_date", QVariant.String),
                        QgsField("last_decision_date", QVariant.String),
                        QgsField("decision_type", QVariant.String),
                        QgsField("decider", QVariant.String),
                        QgsField("notice_number_s", QVariant.String),
                        QgsField("notice_number_l", QVariant.String),
                    ]
                )
                temp_layer.updateFields()

                # フィーチャの追加
                for feature in layer.getFeatures():
                    if self.check_canceled():
                        return  # キャンセルチェック
                    new_feature = QgsFeature()
                    new_feature.setGeometry(feature.geometry())

                    # 属性データのマッピング
                    attributes = [
                        feature["YoutoName"],  # type
                        feature["YoutoCode"],  # type_id
                        feature["FAR"],  # area_ratio
                        feature["BCR"],  # bulding_coverage_ratio
                        feature["Pref"],  # prefecture_name
                        feature["Citycode"],  # city_code
                        feature["Cityname"],  # city_name
                        feature["INDate"],  # first_decision_date
                        feature["FNDate"],  # last_decision_date
                        feature["ValidType"],  # decision_type
                        feature["Custodian"],  # decider
                        feature["INNumber"],  # notice_number_s
                        feature["FNNumber"],  # notice_number_l
                    ]
                    new_feature.setAttributes(attributes)
                    temp_provider.addFeature(new_feature)

                layers.append(temp_layer)

            if not layers:
                raise Exception(
                    "有効な用途地域のShapefileが見つかりませんでした。"
                )

            # 複数のレイヤをマージ
            merged_layer = self.__merge_layers(layers)

            # land_use_areasレイヤをGeoPackageに保存
            if not self.gpkg_manager.add_layer(
                merged_layer, "land_use_areas", "用途地域"
            ):
                raise Exception(self.tr("Failed to add layer to GeoPackage."))

            data_name = self.tr("land use area")
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

    def create_hazard_area_planned_scale(self):
        """ハザードエリア計画規模 作成"""
        try:
            # base_path 配下の「15_ハザードエリア計画規模」フォルダを再帰的に探索してShapefileを収集
            induction_area_folder = os.path.join(
                self.base_path, "15_ハザードエリア計画規模"
            )
            shp_files = self.__get_shapefiles(induction_area_folder)

            # レイヤを格納するリスト
            layers = []

            for shp_file in shp_files:
                if self.check_canceled():
                    return  # キャンセルチェック
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
                    "A31b_101",
                }

                if not required_fields.issubset(layer_fields):
                    data_name = self.tr("hazard area planned scales")
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
                    f"Polygon?crs={layer.crs().authid()}",
                    "hazard_area_planned_scales",
                    "memory",
                )
                temp_provider = temp_layer.dataProvider()

                # 必要なフィールドを追加
                temp_provider.addAttributes(
                    [
                        QgsField("rank", QVariant.String),
                    ]
                )
                temp_layer.updateFields()

                # フィーチャの追加
                for feature in layer.getFeatures():
                    if self.check_canceled():
                        return  # キャンセルチェック
                    new_feature = QgsFeature()
                    new_feature.setGeometry(feature.geometry())

                    # 属性データのマッピング
                    attributes = [
                        feature["A31b_101"],  # rank
                    ]
                    new_feature.setAttributes(attributes)
                    temp_provider.addFeature(new_feature)

                layers.append(temp_layer)

            if not layers:
                data_name = self.tr("hazard area planned scales")
                msg = (
                    self.tr("No valid %1 Shapefile was found.")
                    .replace("%1", data_name)
                )
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Info,
                )

                # 一時メモリレイヤを作成
                temp_layer = QgsVectorLayer(
                    "Polygon", "hazard_area_planned_scales", "memory"
                )
                temp_provider = temp_layer.dataProvider()

                # 必要なフィールドを追加
                temp_provider.addAttributes(
                    [
                        QgsField("rank", QVariant.String),
                    ]
                )
                temp_layer.updateFields()
                layers.append(temp_layer)

            # 複数のレイヤをマージ
            merged_layer = self.__merge_layers(layers)

            # 無効なジオメトリを修正する
            merged_layer = self.__fix_invalid_geometries(merged_layer)

            # 空間インデックス作成
            processing.run("native:createspatialindex",
                           {'INPUT': merged_layer})

            # ゾーンポリゴンを読み込む
            zones_layer = self.gpkg_manager.load_layer(
                'zones', None, withload_project=False
            )

            # 空間インデックス作成
            processing.run(
                "native:createspatialindex", {'INPUT': zones_layer}
            )

            # ゾーンポリゴン範囲と交差するエリアのみを抽出
            extracted_layer = processing.run(
                "native:extractbylocation",
                {
                    'INPUT': merged_layer,
                    'PREDICATE': [0],  # intersects
                    'INTERSECT': zones_layer,
                    'OUTPUT': 'TEMPORARY_OUTPUT',
                },
            )['OUTPUT']

            # hazard_area_planned_scalesレイヤをGeoPackageに保存
            if not self.gpkg_manager.add_layer(
                extracted_layer,
                "hazard_area_planned_scales",
                "洪水浸水想定区域_計画規模_L1",
            ):
                raise Exception(self.tr("Failed to add layer to GeoPackage."))

            data_name = self.tr("hazard area planned scale")
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

    def create_hazard_area_max_scale(self):
        """ハザードエリア想定最大規模 作成"""
        try:
            # base_path 配下の「16_ハザードエリア想定最大規模」フォルダを再帰的に探索してShapefileを収集
            induction_area_folder = os.path.join(
                self.base_path, "16_ハザードエリア想定最大規模"
            )
            shp_files = self.__get_shapefiles(induction_area_folder)

            # レイヤを格納するリスト
            layers = []

            for shp_file in shp_files:
                if self.check_canceled():
                    return  # キャンセルチェック
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
                    "A31b_201",
                }

                if not required_fields.issubset(layer_fields):
                    data_name = self.tr("hazard area maximum scale")
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
                    f"Polygon?crs={layer.crs().authid()}",
                    "hazard_area_maximum_scales",
                    "memory",
                )
                temp_provider = temp_layer.dataProvider()

                # 必要なフィールドを追加
                temp_provider.addAttributes(
                    [
                        QgsField("rank", QVariant.String),
                    ]
                )
                temp_layer.updateFields()

                # フィーチャの追加
                for feature in layer.getFeatures():
                    if self.check_canceled():
                        return  # キャンセルチェック
                    new_feature = QgsFeature()
                    new_feature.setGeometry(feature.geometry())

                    # 属性データのマッピング
                    attributes = [
                        feature["A31b_201"],  # rank
                    ]
                    new_feature.setAttributes(attributes)
                    temp_provider.addFeature(new_feature)

                layers.append(temp_layer)

            if not layers:
                data_name = self.tr("hazard area maximum scale")
                msg = (
                    self.tr("No valid %1 Shapefile was found.")
                    .replace("%1", data_name)
                )
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Info,
                )

                # 一時メモリレイヤを作成
                temp_layer = QgsVectorLayer(
                    "Polygon", "hazard_area_maximum_scales", "memory"
                )
                temp_provider = temp_layer.dataProvider()

                # 必要なフィールドを追加
                temp_provider.addAttributes(
                    [
                        QgsField("rank", QVariant.String),
                    ]
                )
                temp_layer.updateFields()
                layers.append(temp_layer)

            # 複数のレイヤをマージ
            merged_layer = self.__merge_layers(layers)

            # 無効なジオメトリを修正する
            merged_layer = self.__fix_invalid_geometries(merged_layer)

            # 空間インデックス作成
            processing.run("native:createspatialindex",
                           {'INPUT': merged_layer})

            # ゾーンポリゴンを読み込む
            zones_layer = self.gpkg_manager.load_layer(
                'zones', None, withload_project=False
            )

            # 空間インデックス作成
            processing.run(
                "native:createspatialindex", {'INPUT': zones_layer}
            )

            # ゾーンポリゴン範囲と交差するエリアのみを抽出
            extracted_layer = processing.run(
                "native:extractbylocation",
                {
                    'INPUT': merged_layer,
                    'PREDICATE': [0],  # intersects
                    'INTERSECT': zones_layer,
                    'OUTPUT': 'TEMPORARY_OUTPUT',
                },
            )['OUTPUT']

            # hazard_area_maximum_scalesレイヤをGeoPackageに保存
            if not self.gpkg_manager.add_layer(
                extracted_layer,
                "hazard_area_maximum_scales",
                "洪水浸水想定区域_想定最大規模_L2",
            ):
                raise Exception(self.tr("Failed to add layer to GeoPackage."))

            data_name = self.tr("hazard area maximum scale")
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

    def create_hazard_area_storm_surge(self):
        """ハザードエリア高潮浸水想定区域 作成"""
        try:
            # base_path 配下の「17_ハザードエリア高潮浸水想定区域」フォルダを再帰的に探索してShapefileを収集
            induction_area_folder = os.path.join(
                self.base_path, "17_ハザードエリア高潮浸水想定区域"
            )
            shp_files = self.__get_shapefiles(induction_area_folder)

            # レイヤを格納するリスト
            layers = []

            required_fields = {
                "A49_001",
                "A49_002",
                "A49_003",
            }

            for shp_file in shp_files:
                if self.check_canceled():
                    return  # キャンセルチェック
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
                    "A49_001",
                    "A49_002",
                    "A49_003",
                }

                if not required_fields.issubset(layer_fields):
                    data_name = self.tr("hazard area storm surge")
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
                    f"Polygon?crs={layer.crs().authid()}",
                    "hazard_area_storm_surges",
                    "memory",
                )
                temp_provider = temp_layer.dataProvider()

                # 必要なフィールドを追加
                temp_provider.addAttributes(
                    [
                        QgsField("prefecture_name", QVariant.String),
                        QgsField("prefecture_code", QVariant.String),
                        QgsField("rank", QVariant.String),
                    ]
                )
                temp_layer.updateFields()

                # フィーチャの追加
                for feature in layer.getFeatures():
                    if self.check_canceled():
                        return  # キャンセルチェック
                    new_feature = QgsFeature()
                    new_feature.setGeometry(feature.geometry())

                    # 属性データのマッピング
                    attributes = [
                        feature["A49_001"],  # prefecture_name
                        feature["A49_002"],  # prefecture_code
                        feature["A49_003"],  # rank
                    ]
                    new_feature.setAttributes(attributes)
                    temp_provider.addFeature(new_feature)

                layers.append(temp_layer)

            if not layers:
                data_name = self.tr("hazard area storm surge")
                msg = (
                    self.tr("No valid %1 Shapefile was found.")
                    .replace("%1", data_name)
                )
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Info,
                )

                # 一時メモリレイヤを作成
                temp_layer = QgsVectorLayer(
                    "Polygon", "hazard_area_storm_surges", "memory"
                )
                temp_provider = temp_layer.dataProvider()

                # 必要なフィールドを追加
                temp_provider.addAttributes(
                    [
                        QgsField("prefecture_name", QVariant.String),
                        QgsField("prefecture_code", QVariant.String),
                        QgsField("rank", QVariant.String),
                    ]
                )
                temp_layer.updateFields()
                layers.append(temp_layer)

            # 複数のレイヤをマージ
            merged_layer = self.__merge_layers(layers)

            # 無効なジオメトリを修正する
            merged_layer = self.__fix_invalid_geometries(merged_layer)

            # 空間インデックス作成
            processing.run("native:createspatialindex",
                           {'INPUT': merged_layer})

            # ゾーンポリゴンを読み込む
            zones_layer = self.gpkg_manager.load_layer(
                'zones', None, withload_project=False
            )

            # 空間インデックス作成
            processing.run(
                "native:createspatialindex", {'INPUT': zones_layer}
            )

            # ゾーンポリゴン範囲と交差するエリアのみを抽出
            extracted_layer = processing.run(
                "native:extractbylocation",
                {
                    'INPUT': merged_layer,
                    'PREDICATE': [0],  # intersects
                    'INTERSECT': zones_layer,
                    'OUTPUT': 'TEMPORARY_OUTPUT',
                },
            )['OUTPUT']

            # hazard_area_storm_surgesレイヤをGeoPackageに保存
            if not self.gpkg_manager.add_layer(
                extracted_layer, "hazard_area_storm_surges", "高潮浸水想定区域"
            ):
                raise Exception(self.tr("Failed to add layer to GeoPackage."))

            data_name = self.tr("hazard area storm surge")
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

    def create_hazard_area_tsunami(self):
        """ハザードエリア津波浸水想定区域 作成"""
        try:
            # base_path 配下の「18_ハザードエリア津波浸水想定区域」フォルダを再帰的に探索してShapefileを収集
            induction_area_folder = os.path.join(
                self.base_path, "18_ハザードエリア津波浸水想定区域"
            )
            shp_files = self.__get_shapefiles(induction_area_folder)

            # レイヤを格納するリスト
            layers = []

            for shp_file in shp_files:
                if self.check_canceled():
                    return  # キャンセルチェック
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
                    "A40_001",
                    "A40_002",
                    "A40_003",
                }

                if not required_fields.issubset(layer_fields):
                    data_name = self.tr("hazard area tsunami")
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
                    f"Polygon?crs={layer.crs().authid()}",
                    "hazard_area_tsunamis",
                    "memory",
                )
                temp_provider = temp_layer.dataProvider()

                # 必要なフィールドを追加
                temp_provider.addAttributes(
                    [
                        QgsField("prefecture_name", QVariant.String),
                        QgsField("prefecture_code", QVariant.String),
                        QgsField("rank", QVariant.String),
                    ]
                )
                temp_layer.updateFields()

                # フィーチャの追加
                for feature in layer.getFeatures():
                    if self.check_canceled():
                        return  # キャンセルチェック
                    new_feature = QgsFeature()
                    new_feature.setGeometry(feature.geometry())

                    # 属性データのマッピング
                    attributes = [
                        feature["A40_001"],  # prefecture_name
                        feature["A40_002"],  # prefecture_code
                        feature["A40_003"],  # rank
                    ]
                    new_feature.setAttributes(attributes)
                    temp_provider.addFeature(new_feature)

                layers.append(temp_layer)

            if not layers:
                data_name = self.tr("hazard area tsunami")
                msg = (
                    self.tr("No valid %1 Shapefile was found.")
                    .replace("%1", data_name)
                )
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Info,
                )

                # 一時メモリレイヤを作成
                temp_layer = QgsVectorLayer(
                    "Polygon", "hazard_area_tsunamis", "memory"
                )
                temp_provider = temp_layer.dataProvider()

                # 必要なフィールドを追加
                temp_provider.addAttributes(
                    [
                        QgsField("prefecture_name", QVariant.String),
                        QgsField("prefecture_code", QVariant.String),
                        QgsField("rank", QVariant.String),
                    ]
                )
                temp_layer.updateFields()
                layers.append(temp_layer)

            # 複数のレイヤをマージ
            merged_layer = self.__merge_layers(layers)

            # 無効なジオメトリを修正する
            merged_layer = self.__fix_invalid_geometries(merged_layer)

            # 空間インデックス作成
            processing.run("native:createspatialindex",
                           {'INPUT': merged_layer})

            # ゾーンポリゴンを読み込む
            zones_layer = self.gpkg_manager.load_layer(
                'zones', None, withload_project=False
            )

            # 空間インデックス作成
            processing.run(
                "native:createspatialindex", {'INPUT': zones_layer}
            )

            # ゾーンポリゴン範囲と交差するエリアのみを抽出
            extracted_layer = processing.run(
                "native:extractbylocation",
                {
                    'INPUT': merged_layer,
                    'PREDICATE': [0],  # intersects
                    'INTERSECT': zones_layer,
                    'OUTPUT': 'TEMPORARY_OUTPUT',
                },
            )['OUTPUT']

            # hazard_area_tsunamisレイヤをGeoPackageに保存
            if not self.gpkg_manager.add_layer(
                extracted_layer, "hazard_area_tsunamis", "津波浸水想定区域"
            ):
                raise Exception(self.tr("Failed to add layer to GeoPackage."))

            data_name = self.tr("hazard area tsunami")
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

    def create_hazard_area_landslide(self):
        """ハザードエリア土砂災害 作成"""
        try:
            # base_path 配下の「19_ハザードエリア土砂災害」フォルダを再帰的に探索してShapefileを収集
            induction_area_folder = os.path.join(
                self.base_path, "19_ハザードエリア土砂災害"
            )
            shp_files = self.__get_shapefiles(induction_area_folder)

            # レイヤを格納するリスト
            layers = []

            for shp_file in shp_files:
                if self.check_canceled():
                    return  # キャンセルチェック
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
                    "A33_001",
                    "A33_002",
                    "A33_004",
                    "A33_005",
                    "A33_006",
                    "A33_007",
                    "A33_008",
                }

                if not required_fields.issubset(layer_fields):
                    data_name = self.tr("shelter")
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
                    f"Polygon?crs={layer.crs().authid()}",
                    "hazard_area_landslides",
                    "memory",
                )
                temp_provider = temp_layer.dataProvider()

                # 必要なフィールドを追加
                temp_provider.addAttributes(
                    [
                        QgsField("phenomenon_type", QVariant.String),
                        QgsField("area_type", QVariant.String),
                        QgsField("prefecture_code", QVariant.String),
                        QgsField("area_number", QVariant.String),
                        QgsField("area_name", QVariant.String),
                        QgsField("address", QVariant.String),
                        QgsField("public_date", QVariant.String),
                        QgsField("designated_flag", QVariant.String),
                    ]
                )
                temp_layer.updateFields()

                # フィーチャの追加
                for feature in layer.getFeatures():
                    if self.check_canceled():
                        return  # キャンセルチェック
                    new_feature = QgsFeature()
                    new_feature.setGeometry(feature.geometry())

                    # 属性データのマッピング
                    attributes = [
                        feature["A33_001"],  # phenomenon_type
                        feature["A33_002"],  # area_type
                        feature["A33_004"],  # area_number
                        feature["A33_005"],  # area_name
                        feature["A33_006"],  # address
                        feature["A33_007"],  # public_date
                        feature["A33_008"],  # designated_flag
                    ]
                    new_feature.setAttributes(attributes)
                    temp_provider.addFeature(new_feature)

                layers.append(temp_layer)

            if not layers:
                data_name = self.tr("hazard area landslide")
                msg = (
                    self.tr("No valid %1 Shapefile was found.")
                    .replace("%1", data_name)
                )
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Info,
                )

                # 一時メモリレイヤを作成
                temp_layer = QgsVectorLayer(
                    "Polygon", "hazard_area_landslides", "memory"
                )
                temp_provider = temp_layer.dataProvider()

                # 必要なフィールドを追加
                temp_provider.addAttributes(
                    [
                        QgsField("phenomenon_type", QVariant.String),
                        QgsField("area_type", QVariant.String),
                        QgsField("prefecture_code", QVariant.String),
                        QgsField("area_number", QVariant.String),
                        QgsField("area_name", QVariant.String),
                        QgsField("address", QVariant.String),
                        QgsField("public_date", QVariant.String),
                        QgsField("designated_flag", QVariant.String),
                    ]
                )
                temp_layer.updateFields()
                layers.append(temp_layer)

            # 複数のレイヤをマージ
            merged_layer = self.__merge_layers(layers)

            # 無効なジオメトリを修正する
            merged_layer = self.__fix_invalid_geometries(merged_layer)

            # 空間インデックス作成
            processing.run("native:createspatialindex",
                           {'INPUT': merged_layer})

            # ゾーンポリゴンを読み込む
            zones_layer = self.gpkg_manager.load_layer(
                'zones', None, withload_project=False
            )

            # 空間インデックス作成
            processing.run(
                "native:createspatialindex", {'INPUT': zones_layer}
            )

            # ゾーンポリゴン範囲と交差するエリアのみを抽出
            extracted_layer = processing.run(
                "native:extractbylocation",
                {
                    'INPUT': merged_layer,
                    'PREDICATE': [0],  # intersects
                    'INTERSECT': zones_layer,
                    'OUTPUT': 'TEMPORARY_OUTPUT',
                },
            )['OUTPUT']

            # hazard_area_landslidesレイヤをGeoPackageに保存
            if not self.gpkg_manager.add_layer(
                extracted_layer, "hazard_area_landslides", "土砂災害警戒区域"
            ):
                raise Exception(self.tr("Failed to add layer to GeoPackage."))

            data_name = self.tr("hazard area landslide")
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

    def create_hazard_area_floodplain(self):
        """ハザードエリア氾濫流 作成"""
        try:
            # base_path 配下の「20_ハザードエリア氾濫流」フォルダを再帰的に探索してShapefileを収集
            induction_area_folder = os.path.join(
                self.base_path, "20_ハザードエリア氾濫流"
            )
            shp_files = self.__get_shapefiles(induction_area_folder)

            # レイヤを格納するリスト
            layers = []

            for shp_file in shp_files:
                if self.check_canceled():
                    return  # キャンセルチェック
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
                    "A31b_401",
                }

                if not required_fields.issubset(layer_fields):
                    data_name = self.tr("hazard area floodplain")
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
                    f"Polygon?crs={layer.crs().authid()}",
                    "hazard_area_floodplains",
                    "memory",
                )
                temp_provider = temp_layer.dataProvider()

                # 必要なフィールドを追加
                temp_provider.addAttributes(
                    [
                        QgsField("rank", QVariant.String),
                    ]
                )
                temp_layer.updateFields()

                # フィーチャの追加
                for feature in layer.getFeatures():
                    if self.check_canceled():
                        return  # キャンセルチェック
                    new_feature = QgsFeature()
                    new_feature.setGeometry(feature.geometry())

                    # 属性データのマッピング
                    attributes = [
                        feature["A31b_401"],  # rank
                    ]
                    new_feature.setAttributes(attributes)
                    temp_provider.addFeature(new_feature)

                layers.append(temp_layer)

            if not layers:
                data_name = self.tr("hazard area floodplain")
                msg = (
                    self.tr("No valid %1 Shapefile was found.")
                    .replace("%1", data_name)
                )
                QgsMessageLog.logMessage(
                    msg,
                    self.tr("Plugin"),
                    Qgis.Info,
                )

                # 一時メモリレイヤを作成
                temp_layer = QgsVectorLayer(
                    "Polygon", "hazard_area_floodplains", "memory"
                )
                temp_provider = temp_layer.dataProvider()

                # 必要なフィールドを追加
                temp_provider.addAttributes(
                    [
                        QgsField("rank", QVariant.String),
                    ]
                )
                temp_layer.updateFields()
                layers.append(temp_layer)

            # 複数のレイヤをマージ
            merged_layer = self.__merge_layers(layers)

            # 無効なジオメトリを修正する
            merged_layer = self.__fix_invalid_geometries(merged_layer)

            # 空間インデックス作成
            processing.run("native:createspatialindex",
                           {'INPUT': merged_layer})

            # ゾーンポリゴンを読み込む
            zones_layer = self.gpkg_manager.load_layer(
                'zones', None, withload_project=False
            )

            # 空間インデックス作成
            processing.run(
                "native:createspatialindex", {'INPUT': zones_layer}
            )

            # ゾーンポリゴン範囲と交差するエリアのみを抽出
            extracted_layer = processing.run(
                "native:extractbylocation",
                {
                    'INPUT': merged_layer,
                    'PREDICATE': [0],  # intersects
                    'INTERSECT': zones_layer,
                    'OUTPUT': 'TEMPORARY_OUTPUT',
                },
            )['OUTPUT']

            # hazard_area_floodplainsレイヤをGeoPackageに保存
            if not self.gpkg_manager.add_layer(
                extracted_layer,
                "hazard_area_floodplains",
                "洪水浸水想定区域_氾濫流",
            ):
                raise Exception(self.tr("Failed to add layer to GeoPackage."))

            data_name = self.tr("hazard area floodplain")
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
            with open(dbf_file, 'rb') as f:
                raw_data = f.read()
                result = chardet.detect(raw_data)
                encoding = result.get('encoding')

                # encodingがNoneの場合はデフォルトをUTF-8に
                if not encoding:
                    encoding = 'UTF-8'

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

                if encoding == 'Windows-1252':
                    msg = self.tr(
                        "%1 was detected. Using SHIFT_JIS for the file %2."
                    ).replace("%1", "Windows-1252").replace("%2", dbf_file)
                    QgsMessageLog.logMessage(
                        msg,
                        self.tr("Plugin"),
                        Qgis.Info,
                    )
                    encoding = 'SHIFT_JIS'

                msg = self.tr("Encoding: %1").replace("%1", encoding)
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

    def update_induction_area(self):
        """変更後モード用: 居住誘導区域を更新"""
        try:
            # 変更後GPKGからinduction_areasレイヤを読み込み
            induction_layer = self.gpkg_manager.load_layer(
                "induction_areas", "誘導区域", withload_project=False
            )

            if not induction_layer or not induction_layer.isValid():
                raise Exception("induction_areasレイヤの読み込みに失敗しました。")

            # type_id=31（居住誘導区域）以外のフィーチャを新しいメモリレイヤにコピー
            memory_layer = QgsVectorLayer(
                f"Polygon?crs={induction_layer.crs().authid()}",
                "induction_areas",
                "memory"
            )
            memory_layer.dataProvider().addAttributes(induction_layer.fields())
            memory_layer.updateFields()

            # type_id != 31 のフィーチャをコピー
            features_to_keep = []
            deleted_count = 0
            for feature in induction_layer.getFeatures():
                if feature["type_id"] != 31:
                    new_feature = QgsFeature(memory_layer.fields())
                    new_feature.setGeometry(feature.geometry())
                    new_feature.setAttributes(feature.attributes())
                    features_to_keep.append(new_feature)
                else:
                    deleted_count += 1

            memory_layer.dataProvider().addFeatures(features_to_keep)

            # 変更後誘導区域フォルダからShapefileを読み込み
            shp_files = self.__get_shapefiles(self.induction_area_folder)

            if not shp_files:
                raise Exception(
                    f"変更後誘導区域フォルダにShapefileが見つかりません: {self.induction_area_folder}"
                )

            # 新しい居住誘導区域（type_id=31）を追加
            for shp_file in shp_files:
                if self.check_canceled():
                    return

                encoding = self.__detect_encoding(shp_file)
                layer = QgsVectorLayer(shp_file, os.path.basename(shp_file), "ogr")
                layer.setProviderEncoding(encoding)

                if not layer.isValid():
                    QgsMessageLog.logMessage(
                        f"Shapefileの読み込みに失敗: {shp_file}",
                        self.tr("Plugin"),
                        Qgis.Warning,
                    )
                    continue

                # フィーチャをtype_id=31として追加
                for feature in layer.getFeatures():
                    if self.check_canceled():
                        return

                    # ジオメトリの妥当性チェックと修正
                    geom = feature.geometry()
                    if not geom.isGeosValid():
                        geom = geom.makeValid()
                        QgsMessageLog.logMessage(
                            f"無効なジオメトリを修正しました: {shp_file}",
                            self.tr("Plugin"),
                            Qgis.Warning,
                        )

                    new_feature = QgsFeature(memory_layer.fields())
                    new_feature.setGeometry(geom)

                    # 属性を設定（type_id=31固定）
                    new_feature["type_id"] = 31
                    new_feature["type"] = "居住誘導区域"

                    # その他のフィールドがあれば設定
                    if "Pref" in layer.fields().names():
                        new_feature["prefecture_name"] = feature["Pref"]
                    if "Citycode" in layer.fields().names():
                        new_feature["city_code"] = feature["Citycode"]
                    if "Cityname" in layer.fields().names():
                        new_feature["city_name"] = feature["Cityname"]

                    memory_layer.dataProvider().addFeatures([new_feature])

            # 更新したメモリレイヤをGeoPackageに保存
            if not self.gpkg_manager.add_layer(
                memory_layer, "induction_areas", "誘導区域", withload_project=False
            ):
                raise Exception("更新したinduction_areasレイヤの保存に失敗しました。")

            QgsMessageLog.logMessage(
                "変更後の居住誘導区域を更新しました。",
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
