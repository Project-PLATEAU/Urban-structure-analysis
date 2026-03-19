"""
/***************************************************************************
 *
 * 【FN001】フォルダ生成機能
 * 使用例
 * dir_maker = DirMaker("{ディレクトリパス}")
 * dir_maker.create_structure()
 *
 ***************************************************************************/
"""

import os
from qgis.core import QgsMessageLog, Qgis
from PyQt5.QtCore import QCoreApplication

class DirMaker:
    """フォルダ生成機能"""
    def __init__(self, base_path):
        self.base_path = base_path

    def tr(self, message):
        """翻訳用のメソッド"""
        return QCoreApplication.translate(self.__class__.__name__, message)

    def create_structure(self):
        """フォルダ生成処理"""
        try:
            # 作成するフォルダのリスト
            directories = [
                "01_都市モデル（建物）",
                "02_ゾーンポリゴン",
                "03_鉄道駅位置",
                "04_鉄道ネットワーク",
                "05_バス停",
                "06_バスルート",
                "07_道路ネットワーク",
                "08_施設/1_行政機能/設定年",
                "08_施設/1_行政機能/最新年",
                "08_施設/2_文化交流機能/設定年",
                "08_施設/2_文化交流機能/最新年",
                "08_施設/3_介護・福祉機能/設定年",
                "08_施設/3_介護・福祉機能/最新年",
                "08_施設/4_医療機能/設定年",
                "08_施設/4_医療機能/最新年",
                "08_施設/5_教育機能/設定年",
                "08_施設/5_教育機能/最新年",
                "08_施設/6_子育て機能/設定年",
                "08_施設/6_子育て機能/最新年",
                "08_施設/7_商業機能/設定年",
                "08_施設/7_商業機能/最新年",
                "08_施設/8_都市機能誘導施設/設定年",
                "08_施設/8_都市機能誘導施設/最新年",
                "09_避難所",
                "10_250mメッシュ",
                "11_250mメッシュ人口/2010年",
                "11_250mメッシュ人口/2015年",
                "11_250mメッシュ人口/2020年",
                "12_500mメッシュ別将来人口",
                "13_変化度マップ（建物変化）",
                "14_土地利用細分化メッシュ",
                "15_ハザードエリア計画規模",
                "16_ハザードエリア想定最大規模",
                "17_ハザードエリア高潮浸水想定区域",
                "18_ハザードエリア津波浸水想定区域",
                "19_ハザードエリア土砂災害",
                "20_ハザードエリア氾濫流",
                "21_誘導区域",
                "22_仮想居住誘導区域",
                "23_地価公示/2010年度",
                "23_地価公示/2015年度",
                "23_地価公示/2020年度",
                "24_空き家ポイント",
                "25_固定資産の価格等の概要調書",
                "26_市町村別決算状況調",
                "27_人口集中地区",
            ]

            for directory in directories:
                dir_path = os.path.join(self.base_path, directory)
                os.makedirs(dir_path, exist_ok=True)

            # 固定資産の価格等の概要調書配下に年度フォルダを作成
            fixed_asset_base = os.path.join(self.base_path, "25_固定資産の価格等の概要調書")
            fixed_asset_years = ["2010年度", "2015年度", "2020年度"]
            for year in fixed_asset_years:
                year_path = os.path.join(fixed_asset_base, year)
                os.makedirs(year_path, exist_ok=True)

            # 市町村別決算状況調配下に年度フォルダを作成
            settlement_base = os.path.join(self.base_path, "26_市町村別決算状況調")
            settlement_years = [f"{year}年度" for year in range(2012, 2023)]
            for year in settlement_years:
                year_path = os.path.join(settlement_base, year)
                os.makedirs(year_path, exist_ok=True)

            # 目標人口設定ファイルを作成
            csv_path = os.path.join(
                self.base_path, "population_target_setting.csv"
            )
            with open(csv_path, mode='w', encoding='cp932') as file:
                file.write("比較将来年度,目標人口\n")

            # 建物属性対応表.csvを作成
            building_attr_csv_path = os.path.join(
                self.base_path, "01_都市モデル（建物）", "建物属性対応表.csv"
            )
            with open(building_attr_csv_path, mode='w', encoding='cp932') as file:
                file.write("変換先項目説明,変換先項目名称,建物利用現況調査結果データ項目名称\n")
                file.write("建物用途,usage,\n")
                file.write("地上階数,storeysAboveGround,\n")
                file.write("地下階数,storeysBelowGround,\n")
                file.write("延床面積（㎡）,totalFloorArea,\n")
                file.write("建築年（年：西 ）,yearOfConstruction,\n")

            # 建物用途対応表.csvを作成
            building_usage_csv_path = os.path.join(
                self.base_path, "01_都市モデル（建物）", "建物用途対応表.csv"
            )
            with open(building_usage_csv_path, mode='w', encoding='cp932') as file:
                file.write("変換先項目説明,変換先項目名称,建物用途データ値\n")
                file.write("専用住宅,住宅,\n")
                file.write("アパート、マンション、長屋、寮等,共同住宅,\n")
                file.write("商業施設,店舗,\n")
                file.write("住宅と商業施設等の併用,店舗等併用住宅,\n")
                file.write("共同住宅と商業施設等の併用,店舗等併用共同住宅,\n")
                file.write("住宅と作業所等の併用,作業所併用住宅,\n")

            # フォルダ構成作成成功のログ出力
            msg = self.tr(
                "Created folder structure at %1."
            ).replace("%1", self.base_path)
            QgsMessageLog.logMessage(
                msg,
                self.tr("Plugin"),
                Qgis.Info,
            )
            return True
        except Exception as e:
            # エラーメッセージのログ出力
            QgsMessageLog.logMessage(
                self.tr("An error occurred: %1").replace("%1", str(e)),
                self.tr("Plugin"),
                Qgis.Critical,
            )
            raise e
