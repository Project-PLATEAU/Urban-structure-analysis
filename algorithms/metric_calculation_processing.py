"""
/***************************************************************************
 *
 * 評価指標算出機能
 *
 ***************************************************************************/
"""
import os
import traceback
from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingParameterString,
    QgsProcessingParameterNumber,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFolderDestination,
    QgsProcessingException,
    QgsMessageLog,
    Qgis
)
from qgis.PyQt.QtCore import QCoreApplication
from PyQt5.QtWidgets import QApplication
from .utils import (
    GpkgManager,
    ZoneDataGenerator,
    VacancyDataGenerator,
    DataLoader,
    PopulationDataGenerator,
    FacilityDataGenerator,
    TransportationDataGenerator,
    BuildingDataAssigner,
    AreaDataGenerator,
    FinancialDataGenerator,
    ResidentialInductionMetricCalculator,
    UrbanFunctionInductionMetricCalculator,
    PublicTransportMetricCalculator,
    FiscalMetricCalculator,
    LandUseMetricCalculator,
    DisasterPreventionMetricCalculator,
)
from .utils.data_loader import BuildingLayerNotFoundError
from .utils.dialog_helper import DialogManager


class MetricCalculationProcessing(QgsProcessingAlgorithm):
    """
    評価指標算出
    """

    INPUT_FOLDER = 'INPUT_FOLDER'
    OUTPUT_FOLDER = 'OUTPUT_FOLDER'
    THRESHOLD_BUS = 'THRESHOLD_BUS'
    THRESHOLD_RAILWAY = 'THRESHOLD_RAILWAY'
    THRESHOLD_SHELTER = 'THRESHOLD_SHELTER'
    IS_AFTER_CHANGE = 'IS_AFTER_CHANGE'
    INDUCTION_AREA_FOLDER = 'INDUCTION_AREA_FOLDER'
    BEFORE_OUTPUT_FOLDER = 'BEFORE_OUTPUT_FOLDER'

    def tr(self, message):
        """翻訳用のメソッド"""
        return QCoreApplication.translate(self.__class__.__name__, message)

    def createInstance(self):
        return MetricCalculationProcessing()

    def name(self):
        return 'metric_calculation'

    def displayName(self):
        return self.tr('評価指標算出')

    def group(self):
        return self.tr('Plateau統計可視化')

    def groupId(self):
        return 'plateau_statistics'

    def shortHelpString(self):
        return self.tr('都市構造分析の評価指標を算出します')

    def initAlgorithm(self, config=None):
        """アルゴリズムのパラメータを初期化"""

        self.addParameter(
            QgsProcessingParameterString(
                self.INPUT_FOLDER,
                self.tr('入力フォルダパス'),
                defaultValue=''
            )
        )

        self.addParameter(
            QgsProcessingParameterFolderDestination(
                self.OUTPUT_FOLDER,
                self.tr('出力フォルダ')
            )
        )

        self.addParameter(
            QgsProcessingParameterNumber(
                self.THRESHOLD_BUS,
                self.tr('バス停閾値'),
                defaultValue=300
            )
        )

        self.addParameter(
            QgsProcessingParameterNumber(
                self.THRESHOLD_RAILWAY,
                self.tr('鉄道駅閾値'),
                defaultValue=800
            )
        )

        self.addParameter(
            QgsProcessingParameterNumber(
                self.THRESHOLD_SHELTER,
                self.tr('避難所閾値'),
                defaultValue=500
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.IS_AFTER_CHANGE,
                self.tr('変更後フラグ'),
                defaultValue=False
            )
        )

        self.addParameter(
            QgsProcessingParameterString(
                self.INDUCTION_AREA_FOLDER,
                self.tr('誘導区域フォルダパス'),
                defaultValue='',
                optional=True
            )
        )

        self.addParameter(
            QgsProcessingParameterString(
                self.BEFORE_OUTPUT_FOLDER,
                self.tr('変更前アウトプットフォルダパス'),
                defaultValue='',
                optional=True
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        """
        評価指標算出機能に含まれる各機能を順次実行します。
        """
        try:
            # パラメータ取得
            input_folder = self.parameterAsString(parameters, self.INPUT_FOLDER, context)
            output_folder = self.parameterAsString(parameters, self.OUTPUT_FOLDER, context)
            threshold_bus = self.parameterAsInt(parameters, self.THRESHOLD_BUS, context)
            threshold_railway = self.parameterAsInt(parameters, self.THRESHOLD_RAILWAY, context)
            threshold_shelter = self.parameterAsInt(parameters, self.THRESHOLD_SHELTER, context)
            is_after_change = self.parameterAsBool(parameters, self.IS_AFTER_CHANGE, context)
            induction_area_folder = self.parameterAsString(parameters, self.INDUCTION_AREA_FOLDER, context)
            before_output_folder = self.parameterAsString(parameters, self.BEFORE_OUTPUT_FOLDER, context)
            file_suffix = "_after" if is_after_change else ""

            # 変更後実行時の前提条件チェック
            if is_after_change:
                feedback.setProgress(0)
                QApplication.processEvents()
                feedback.pushInfo("変更後実行モード: 前提条件をチェック中...")

                # 変更後居住誘導区域データの存在チェック
                if not induction_area_folder or not os.path.exists(induction_area_folder):
                    error_msg = "変更後居住誘導区域データがありません。"
                    feedback.reportError(error_msg)
                    raise QgsProcessingException(error_msg)

                # 変更前のアウトプットパスのチェック
                if not before_output_folder or not os.path.exists(before_output_folder):
                    error_msg = "変更前が未実行です。先に変更前の算出を行ってください。"
                    feedback.reportError(error_msg)
                    raise QgsProcessingException(error_msg)

                # 変更前GeoPackageの存在チェック
                before_gpkg_path = os.path.join(before_output_folder, "PlateauStatisticsVisualizationPlugin.gpkg")
                if not os.path.exists(before_gpkg_path):
                    error_msg = "変更前のGeoPackageがありません。先に変更前の算出を行ってください。"
                    feedback.reportError(error_msg)
                    raise QgsProcessingException(error_msg)

                feedback.pushInfo("前提条件チェック完了")

                # 変更前GpkgManagerを作成
                before_gpkg_manager = GpkgManager(before_output_folder)

                # CSVファイル存在チェックとスキップ確認
                skip_metric_calculation = False
                feedback.pushInfo("変更後CSVファイルの存在をチェック中...")
                QgsMessageLog.logMessage(
                    f"変更後出力フォルダ: {output_folder}",
                    self.tr("Plugin"),
                    Qgis.Info,
                )
                csv_check_result = self.check_existing_csv_files(output_folder, file_suffix)
                QgsMessageLog.logMessage(
                    f"CSVチェック結果: {csv_check_result}",
                    self.tr("Plugin"),
                    Qgis.Info,
                )
                if csv_check_result:
                    feedback.pushInfo("変更後の算出結果ファイルが既に存在します。")

                    # ダイアログを表示してユーザーに確認
                    dialog_manager = DialogManager()
                    skip_metric_calculation = dialog_manager.show_question_dialog(
                        "評価指標算出処理のスキップ確認",
                        "変更後の算出結果ファイルが既に存在します。\n評価指標算出処理をスキップしますか？",
                        "「はい」を選択すると、既存のCSVファイルをそのまま使用します。\n「いいえ」を選択すると、CSVファイルを再計算します。"
                    )

                    if skip_metric_calculation:
                        feedback.pushInfo("評価指標算出処理をスキップします。既存の変更後GeoPackageを使用します。")
                        # スキップ時: 既存のGeoPackageをそのまま使用
                        after_gpkg_manager = GpkgManager(output_folder)

                        # レイヤパネルのチェックのみ行う
                        feedback.pushInfo("変更前のレイヤをチェック中...")
                        self.check_before_layers_in_panel(before_gpkg_manager, feedback)
                        feedback.pushInfo("レイヤチェック完了")
                    else:
                        feedback.pushInfo("評価指標算出処理を実行します。")
                        # 実行時: 変更後GeoPackageを初期化
                        after_gpkg_manager = GpkgManager(output_folder)
                        after_gpkg_manager.make_gpkg()

                        # 変更後GPKGへのコピーも行う
                        feedback.pushInfo("変更前のレイヤをチェック中...")
                        self.load_before_layers(before_gpkg_manager, after_gpkg_manager, feedback)
                        feedback.pushInfo("レイヤチェック完了")
                else:
                    feedback.pushInfo("変更後の算出結果ファイルが存在しません。評価指標算出処理を実行します。")
                    # CSVが存在しない場合: 変更後GeoPackageを初期化
                    after_gpkg_manager = GpkgManager(output_folder)
                    after_gpkg_manager.make_gpkg()

                    # 通常通り実行
                    feedback.pushInfo("変更前のレイヤをチェック中...")
                    self.load_before_layers(before_gpkg_manager, after_gpkg_manager, feedback)
                    feedback.pushInfo("レイヤチェック完了")

                gpkg_manager = after_gpkg_manager
                feedback.setProgress(5)
                QApplication.processEvents()

            else:
                # GeoPackageの初期化
                feedback.setProgress(0)
                QApplication.processEvents()
                gpkg_manager = GpkgManager(output_folder)
                gpkg_manager.make_gpkg()
                feedback.pushInfo(f"GeoPackage path: {gpkg_manager.geopackage_path}")
                feedback.setProgress(5)
                QApplication.processEvents()

                # 変更前実行時はスキップしない
                skip_metric_calculation = False

            # ゾーンポリゴン作成
            if not feedback.isCanceled() and not is_after_change:
                feedback.pushInfo("ゾーンポリゴンを作成中...")
                zone_data_generator = ZoneDataGenerator(
                    input_folder, lambda: feedback.isCanceled(), None, gpkg_manager
                )
                result = zone_data_generator.create_zone()
                if not result:
                    error_msg = "ゾーンポリゴンの作成に失敗しました"
                    feedback.reportError(error_msg)
                    raise Exception(error_msg)
            feedback.setProgress(10)
            QApplication.processEvents()

            # 空き家データ作成
            if not feedback.isCanceled() and not is_after_change:
                feedback.pushInfo("空き家データを作成中...")
                vacancy_data_generator = VacancyDataGenerator(
                    input_folder, lambda: feedback.isCanceled(), gpkg_manager
                )
                vacancy_data_generator.create_vacancy()
            feedback.setProgress(15)
            QApplication.processEvents()

            # データ読み込み機能
            if not feedback.isCanceled() and not is_after_change:
                try:
                    feedback.pushInfo("建物データを読み込み中...")
                    data_loader = DataLoader(lambda: feedback.isCanceled(), input_folder, gpkg_manager)
                    data_loader.load_buildings()
                except BuildingLayerNotFoundError as e:
                    feedback.reportError(str(e))
                    raise QgsProcessingException(str(e))
            feedback.setProgress(20)
            QApplication.processEvents()

            # 人口データ作成機能
            if not feedback.isCanceled() and not is_after_change:
                population_data_generator = PopulationDataGenerator(
                    input_folder, lambda: feedback.isCanceled(), gpkg_manager
                )
                feedback.pushInfo("人口データを作成中...")
                population_data_generator.load_population_meshes()
                # 人口集中地区データ読み込み
                feedback.pushInfo("人口集中地区データを読み込み中...")
                population_data_generator.load_did_data()
            feedback.setProgress(25)
            QApplication.processEvents()

            # 施設関連データ作成機能
            if not feedback.isCanceled() and not is_after_change:
                feedback.pushInfo("施設データを作成中...")
                facility_data_generator = FacilityDataGenerator(
                    input_folder, lambda: feedback.isCanceled(), gpkg_manager
                )
                facility_data_generator.load_facilities()
            feedback.setProgress(30)
            QApplication.processEvents()

            # 交通関連データ作成機能
            if not feedback.isCanceled() and not is_after_change:
                feedback.pushInfo("交通データを作成中...")
                transportation_data_generator = TransportationDataGenerator(
                    input_folder, lambda: feedback.isCanceled(), gpkg_manager
                )
                transportation_data_generator.load_transportations()
            feedback.setProgress(35)
            QApplication.processEvents()

            # 建築物LOD1へのデータ付与機能
            if not feedback.isCanceled() and not is_after_change:
                feedback.pushInfo("建築物データを付与中...")
                building_data_assigner = BuildingDataAssigner(
                    input_folder, lambda: feedback.isCanceled(), gpkg_manager
                )
                building_data_assigner.exec()
            feedback.setProgress(40)
            QApplication.processEvents()

            # 圏域作成機能
            if not feedback.isCanceled():
                feedback.pushInfo("圏域データを作成中...")
                area_data_generator = AreaDataGenerator(
                    input_folder,
                    threshold_bus,
                    threshold_railway,
                    threshold_shelter,
                    lambda: feedback.isCanceled(),
                    gpkg_manager,
                    is_after_change,
                    induction_area_folder,
                )
                area_data_generator.create_area_data()
            feedback.setProgress(45)
            QApplication.processEvents()

            # 財政関連データ作成機能
            if not feedback.isCanceled() and not is_after_change:
                feedback.pushInfo("財政データを作成中...")
                financial_data_generator = FinancialDataGenerator(
                    input_folder, lambda: feedback.isCanceled(), gpkg_manager
                )
                financial_data_generator.create_land_price()
            feedback.setProgress(50)
            QApplication.processEvents()

            # 評価指標算出
            # 居住誘導関連評価指標算出機能
            if not feedback.isCanceled() and not skip_metric_calculation:
                feedback.pushInfo("居住誘導関連評価指標を算出中...")
                calculator = ResidentialInductionMetricCalculator(
                    output_folder, lambda: feedback.isCanceled(), gpkg_manager, file_suffix=file_suffix
                )
                calculator.calc()
            feedback.setProgress(55)
            QApplication.processEvents()

            # 都市機能誘導関連評価指標算出機能
            if not feedback.isCanceled() and not skip_metric_calculation:
                feedback.pushInfo("都市機能誘導関連評価指標を算出中...")
                calculator = UrbanFunctionInductionMetricCalculator(
                    output_folder, lambda: feedback.isCanceled(), gpkg_manager, file_suffix=file_suffix
                )
                calculator.calc()
            feedback.setProgress(65)
            QApplication.processEvents()

            # 防災関連評価指標算出機能
            if not feedback.isCanceled() and not skip_metric_calculation:
                feedback.pushInfo("防災関連評価指標を算出中...")
                calculator = DisasterPreventionMetricCalculator(
                    output_folder, lambda: feedback.isCanceled(), gpkg_manager, file_suffix=file_suffix
                )
                calculator.calc()
            feedback.setProgress(75)
            QApplication.processEvents()

            # 公共交通関連評価指標算出機能
            if not feedback.isCanceled() and not skip_metric_calculation:
                feedback.pushInfo("公共交通関連評価指標を算出中...")
                calculator = PublicTransportMetricCalculator(
                    output_folder, lambda: feedback.isCanceled(), gpkg_manager, file_suffix=file_suffix
                )
                calculator.calc()
            feedback.setProgress(85)
            QApplication.processEvents()

            # 土地利用関連評価指標算出機能
            if not feedback.isCanceled() and not skip_metric_calculation:
                feedback.pushInfo("土地利用関連評価指標を算出中...")
                calculator = LandUseMetricCalculator(
                    output_folder, lambda: feedback.isCanceled(), gpkg_manager, file_suffix=file_suffix
                )
                calculator.calc()
            feedback.setProgress(95)
            QApplication.processEvents()

            # 財政関連評価指標算出機能
            if not feedback.isCanceled() and not skip_metric_calculation:
                feedback.pushInfo("財政関連評価指標を算出中...")
                calculator = FiscalMetricCalculator(
                    input_folder, output_folder, lambda: feedback.isCanceled(), gpkg_manager, file_suffix=file_suffix
                )
                calculator.calc()
            feedback.setProgress(100)
            QApplication.processEvents()

            if not feedback.isCanceled():
                feedback.pushInfo("処理完了")
            else:
                feedback.pushInfo("処理がキャンセルされました")

            # レイヤパネルに追加するGeopackageを返す
            # 変更後実行時は変更前のレイヤを表示するためbefore_gpkg_manager
            # 変更前実行時は現在のgpkg_manager
            layer_gpkg_manager = before_gpkg_manager if is_after_change else gpkg_manager

            return {
                self.OUTPUT_FOLDER: output_folder,
                'gpkg_manager': layer_gpkg_manager
            }

        except Exception as e:
            # ログにはスタックトレースを含む詳細情報を出力
            error_detail = f"{str(e)}\n{traceback.format_exc()}"
            QgsMessageLog.logMessage(
                f"エラーが発生しました: {error_detail}",
                self.tr("Plugin"),
                Qgis.Critical,
            )
            # ダイアログメッセージ
            error_str = str(e) if e else "Unknown error"
            feedback.reportError(f"エラーが発生しました: {error_str}")
            raise QgsProcessingException(error_str)

    def check_existing_csv_files(self, output_folder, file_suffix=""):
        """
        変更後のアウトプットフォルダにIF101~IF107のCSVファイルが存在するかチェック

        :param output_folder: 出力フォルダパス
        :param file_suffix: ファイル名サフィックス（変更後の場合は"_after"）
        :return: 全て存在するかどうか
        """
        csv_files = [
            f'IF101_居住誘導区域関連評価指標ファイル{file_suffix}.csv',
            f'IF102_都市機能誘導区域関連評価指標ファイル{file_suffix}.csv',
            f'IF103_防災関連評価指標ファイル{file_suffix}.csv',
            f'IF104_公共交通関連評価指標ファイル{file_suffix}.csv',
            f'IF105_土地利用関連評価指標ファイル{file_suffix}.csv',
            f'IF106_財政関連評価指標_固定資産税ファイル{file_suffix}.csv',
            f'IF106_財政関連評価指標_歳出額ファイル{file_suffix}.csv',
            f'IF107_将来人口と目標人口の関係性ファイル{file_suffix}.csv',
        ]

        for filename in csv_files:
            file_path = os.path.join(output_folder, filename)
            if not os.path.exists(file_path):
                QgsMessageLog.logMessage(
                    f"CSVファイルが見つかりません: {file_path}",
                    self.tr("Plugin"),
                    Qgis.Info,
                )
                return False

        QgsMessageLog.logMessage(
            f"全てのCSVファイルが存在します（{len(csv_files)}個）",
            self.tr("Plugin"),
            Qgis.Info,
        )
        return True

    def check_before_layers_in_panel(self, before_gpkg_manager, feedback):
        """
        変更前GeoPackageから必要なレイヤをレイヤパネル追加リストに登録

        :param before_gpkg_manager: 変更前GpkgManager
        :param feedback: フィードバックオブジェクト
        """
        # 必要なレイヤ: {レイヤ名: 日本語名}
        required_layers = {
            'buildings': '建築物',
            'bus_networks': 'バスネットワーク',
            'bus_stop_buffers': 'バス停カバー圏域',
            'bus_stops': 'バス停',
            'change_maps': '変化度マップ',
            'facilities': '都市施設',
            'future_population': '将来推計人口メッシュ',
            'hazard_area_landslides': '土砂災害警戒区域',
            'hazard_area_maximum_scales': '洪水浸水想定区域_想定最大規模_L2',
            'hazard_area_planned_scales': '洪水浸水想定区域_計画規模_L1',
            'hazard_area_storm_surges': '高潮浸水想定区域',
            'hazard_area_tsunamis': '津波浸水想定区域',
            'hypothetical_residential_areas': '仮想居住誘導区域',
            'induction_areas': '誘導区域',
            'land_prices': '地価公示',
            'land_use_areas': '用途地域',
            'land_use_maps': '土地利用細分化メッシュ',
            'meshes': '人口メッシュ',
            'railway_networks': '鉄道ネットワーク',
            'railway_station_buffers': '鉄道駅カバー圏域',
            'railway_stations': '鉄道駅',
            'road_networks': '道路ネットワーク',
            'shelter_buffers': '避難施設カバー圏域',
            'shelters': '避難施設',
            'urban_plannings': '都市計画区域',
            'vacancies': '空き家',
            'zones': '行政区域',
            'did': '人口集中地区'
        }

        # induction_areasのフィーチャ数をチェック
        induction_areas_layer = before_gpkg_manager.load_layer('induction_areas', None, withload_project=False)
        has_induction_data = False
        if induction_areas_layer and induction_areas_layer.isValid():
            has_induction_data = induction_areas_layer.featureCount() > 0

        if has_induction_data:
            feedback.pushInfo("induction_areasにデータがあります。induction_areasをレイヤパネルに追加します。")
        else:
            feedback.pushInfo("induction_areasにデータがありません。hypothetical_residential_areasをレイヤパネルに追加します。")

        for layer_name, alias in required_layers.items():
            # キャンセルチェック
            if feedback.isCanceled():
                return

            QApplication.processEvents()

            # レイヤパネルに追加するかどうかを判定
            show_in_panel = True

            # 誘導区域レイヤ、または仮想居住誘導区域レイヤのレイヤパネルへの追加
            if layer_name == 'induction_areas':
                # induction_areasにデータがない場合は、レイヤパネルに追加しない
                if not has_induction_data:
                    show_in_panel = False
            elif layer_name == 'hypothetical_residential_areas':
                # induction_areasにデータがある場合は、レイヤパネルに追加しない
                if has_induction_data:
                    show_in_panel = False

            if show_in_panel:
                # 変更前GPKGから読み込み（layers_to_addリストに追加）
                layer = before_gpkg_manager.load_layer(layer_name, alias, withload_project=True)
                QApplication.processEvents()

                if layer and layer.isValid():
                    feedback.pushInfo(f"レイヤパネル追加リストに登録: {alias}")
                else:
                    # レイヤが存在しない場合はエラー
                    error_msg = f"変更前のGeoPackageに必要なレイヤが不足しています。（不足レイヤ: {alias}）"
                    feedback.reportError(error_msg)
                    raise QgsProcessingException(error_msg)

        feedback.pushInfo("レイヤの読み込みが完了しました")

    def load_before_layers(self, before_gpkg_manager, after_gpkg_manager, feedback):
        """
        変更前GeoPackageから必要なレイヤをレイヤパネルに読み込み、変更後GPKGにコピー

        :param before_gpkg_manager: 変更前GpkgManager
        :param after_gpkg_manager: 変更後GpkgManager
        :param feedback: フィードバックオブジェクト
        """
        # 必要なレイヤ: {レイヤ名: 日本語名}
        required_layers = {
            'buildings': '建築物',
            'bus_networks': 'バスネットワーク',
            'bus_stop_buffers': 'バス停カバー圏域',
            'bus_stops': 'バス停',
            'change_maps': '変化度マップ',
            'facilities': '都市施設',
            'future_population': '将来推計人口メッシュ',
            'hazard_area_landslides': '土砂災害警戒区域',
            'hazard_area_maximum_scales': '洪水浸水想定区域_想定最大規模_L2',
            'hazard_area_planned_scales': '洪水浸水想定区域_計画規模_L1',
            'hazard_area_storm_surges': '高潮浸水想定区域',
            'hazard_area_tsunamis': '津波浸水想定区域',
            'hypothetical_residential_areas': '仮想居住誘導区域',
            'induction_areas': '誘導区域',
            'land_prices': '地価公示',
            'land_use_areas': '用途地域',
            'land_use_maps': '土地利用細分化メッシュ',
            'meshes': '人口メッシュ',
            'population_target_settings': '人口目標設定',
            'railway_networks': '鉄道ネットワーク',
            'railway_station_buffers': '鉄道駅カバー圏域',
            'railway_stations': '鉄道駅',
            'road_networks': '道路ネットワーク',
            'shelter_buffers': '避難施設カバー圏域',
            'shelters': '避難施設',
            'urban_plannings': '都市計画区域',
            'vacancies': '空き家',
            'zones': '行政区域',
            'did': '人口集中地区'
        }

        # 全てのrequired_layersを変更前GPKGから変更後GPKGにコピー
        feedback.pushInfo(f"{len(required_layers)}個のレイヤを変更後GeoPackageにコピーします...")

        # induction_areasのフィーチャ数をチェック
        induction_areas_layer = before_gpkg_manager.load_layer('induction_areas', None, withload_project=False)
        has_induction_data = False
        if induction_areas_layer and induction_areas_layer.isValid():
            has_induction_data = induction_areas_layer.featureCount() > 0

        if has_induction_data:
            feedback.pushInfo("induction_areasにデータがあります。induction_areasをレイヤパネルに追加します。")
        else:
            feedback.pushInfo("induction_areasにデータがありません。hypothetical_residential_areasをレイヤパネルに追加します。")

        for layer_name, alias in required_layers.items():
            # キャンセルチェック
            if feedback.isCanceled():
                return

            QApplication.processEvents()

            # レイヤパネルに追加するかどうかを判定
            # population_target_settingsレイヤは追加しない
            show_in_panel = layer_name != 'population_target_settings'

            # 誘導区域レイヤ、または仮想居住誘導区域レイヤのレイヤパネルへの追加
            if layer_name == 'induction_areas':
                # induction_areasにデータがない場合は、レイヤパネルに追加しない
                if not has_induction_data:
                    show_in_panel = False
            elif layer_name == 'hypothetical_residential_areas':
                # induction_areasにデータがある場合は、レイヤパネルに追加しない
                if has_induction_data:
                    show_in_panel = False

            # 変更前GPKGから読み込み
            layer = before_gpkg_manager.load_layer(layer_name, alias, withload_project=show_in_panel)
            QApplication.processEvents()

            if layer and layer.isValid():
                if show_in_panel:
                    feedback.pushInfo(f"レイヤパネルに追加: {alias}")
                QApplication.processEvents()

                # 変更後GPKGにコピー（レイヤパネルには追加しない）
                result = after_gpkg_manager.add_layer(layer, layer_name, alias, withload_project=False)
                QApplication.processEvents()

                if result:
                    feedback.pushInfo(f"変更後GPKGにコピー: {alias}")
                else:
                    error_msg = f"変更後GeoPackageへのレイヤコピーに失敗しました: {alias}"
                    feedback.reportError(error_msg)
                    raise QgsProcessingException(error_msg)
            else:
                # レイヤが存在しない場合はエラー
                error_msg = f"変更前のGeoPackageに必要なレイヤが不足しています。先に変更前の算出を行ってください。（不足レイヤ: {alias}）"
                feedback.reportError(error_msg)
                raise QgsProcessingException(error_msg)

        feedback.pushInfo("レイヤの読み込みとコピーが完了しました")
