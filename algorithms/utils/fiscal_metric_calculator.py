"""
/***************************************************************************
 *
 * 【FN015】財政関連評価指標算出機能
 *
 ***************************************************************************/
"""

import csv
import os
import pandas as pd
from qgis.core import QgsMessageLog, Qgis
from PyQt5.QtCore import QCoreApplication
from .gpkg_manager import GpkgManager
from .excel_reader import ExcelReader


class FiscalMetricCalculator:
    """財政関連評価指標算出機能"""
    def __init__(self, input_folder, output_folder, check_canceled_callback=None, gpkg_manager=None, file_suffix=""):
        self.input_folder = input_folder  # Excel読み込み用
        self.output_folder = output_folder  # CSV出力用

        self.check_canceled = check_canceled_callback

        self.gpkg_manager = gpkg_manager
        self.file_suffix = file_suffix

        # Excel読み込みヘルパー（メインスレッドで実行）
        self.excel_reader = ExcelReader()

    def tr(self, message):
        """翻訳用のメソッド"""
        return QCoreApplication.translate(self.__class__.__name__, message)

    def calc(self):
        """算出処理"""
        try:
            # ゾーンポリゴンから対象都道府県・市区町村を取得
            zones_layer = self.gpkg_manager.load_layer(
                'zones', None, withload_project=False
            )

            if not zones_layer:
                QgsMessageLog.logMessage(
                    self.tr("zones layer not found. Creating empty output."),
                    self.tr("Plugin"),
                    Qgis.Warning,
                )
                self.export_empty_files()
                return

            # 対象都道府県・市区町村の組み合わせを取得（重複除去）
            target_cities_dict = {}
            for feature in zones_layer.getFeatures():
                if feature["is_target"] == 1:
                    prefecture = feature["prefecture_name"]
                    name = feature["name"]
                    if prefecture and name:
                        # prefecture+nameをキーにして重複を防ぐ
                        key = f"{prefecture}_{name}"
                        if key not in target_cities_dict:
                            target_cities_dict[key] = {
                                'prefecture': str(prefecture),
                                'name': str(name)
                            }

            target_cities = list(target_cities_dict.values())

            if not target_cities:
                QgsMessageLog.logMessage(
                    self.tr("No target cities found. Creating empty output."),
                    self.tr("Plugin"),
                    Qgis.Warning,
                )
                self.export_empty_files()
                return

            # 固定資産税関連データを計算
            land_tax_data = self.calculate_land_tax_data(target_cities)

            # 一人当たり歳出額関連データを計算
            per_capita_data = self.calculate_per_capita_data(target_cities)

            # 固定資産税データを年度別に出力
            fixed_asset_data_list = []
            if 'latest_year' in land_tax_data and land_tax_data['latest_year'] != '―':
                # 利用可能な全年度のデータを出力
                fixed_asset_dir = os.path.join(self.input_folder, "25_固定資産の価格等の概要調書")
                annual_tax_data = {}

                if os.path.exists(fixed_asset_dir):
                    for folder_name in os.listdir(fixed_asset_dir):
                        folder_path = os.path.join(fixed_asset_dir, folder_name)
                        if os.path.isdir(folder_path) and "年度" in folder_name:
                            try:
                                year = int(folder_name.replace("年度", ""))
                                data = self.read_fixed_asset_data(fixed_asset_dir, folder_name)
                                if data is not None:
                                    annual_tax_data[year] = data
                            except ValueError:
                                continue

                # 各年度の税収を計算（万円）
                tax_by_year = {}
                for year, data in annual_tax_data.items():
                    total_tax = 0
                    for city_info in target_cities:
                        tax_revenue = self.extract_tax_base_amount(data, city_info, year)
                        if tax_revenue:
                            total_tax += tax_revenue

                    if total_tax > 0:
                        tax_by_year[year] = total_tax

                # 増減率と増減率の差分を計算
                sorted_years = sorted(tax_by_year.keys())
                change_rates = {}  # 各年の増減率を保持

                for i, year in enumerate(sorted_years):
                    tax_amount = tax_by_year[year]

                    # 前年度からの増減率を計算
                    change_rate = '―'
                    if i > 0:
                        prev_year = sorted_years[i-1]
                        prev_tax = tax_by_year[prev_year]
                        if prev_tax > 0:
                            change_rate = (tax_amount - prev_tax) / prev_tax
                            change_rates[year] = change_rate

                    # 増減率の差分を計算
                    change_rate_delta = '―'
                    if i > 1 and year in change_rates:
                        prev_year = sorted_years[i-1]
                        if prev_year in change_rates:
                            change_rate_delta = change_rates[year] - change_rates[prev_year]

                    fixed_asset_data = {
                        # 年次
                        'year': year,
                        # 固定資産税(土地) 万円
                        'land_fixed_asset_tax': self.round_or_na(tax_amount, 0),
                        # 固定資産税(土地)の前年度からの増減率（実数）
                        'land_fixed_asset_tax_change_rate': self.round_or_na(change_rate, 3) if change_rate != '―' else '―',
                        # 固定資産税(土地)の増減率の差分（実数）
                        'land_fixed_asset_tax_change_rate_delta': self.round_or_na(change_rate_delta, 3) if change_rate_delta != '―' else '―',
                        # 全国平均値
                        'land_fixed_asset_tax_revenue_national_avg': '―',
                        # 都道府県平均値
                        'land_fixed_asset_tax_revenue_pref_avg': '―',
                    }
                    fixed_asset_data_list.append(fixed_asset_data)

            # データがない場合は空データを作成
            if not fixed_asset_data_list:
                empty_fixed_asset_data = {
                    'year': '―',
                    'land_fixed_asset_tax': '―',
                    'land_fixed_asset_tax_change_rate': '―',
                    'land_fixed_asset_tax_change_rate_delta': '―',
                    'land_fixed_asset_tax_revenue_national_avg': '―',
                    'land_fixed_asset_tax_revenue_pref_avg': '―',
                }
                fixed_asset_data_list.append(empty_fixed_asset_data)

            # 歳出額データを期間別に出力
            expenditure_data_list = []


            # 期間別計算（元の実装を使用）
            settlement_dir = os.path.join(self.input_folder, "26_市町村別決算状況調")

            if os.path.exists(settlement_dir):
                # 期間別計算
                periods = [
                    {'id': 1, 'label': '2012-2017', 'years': list(range(2012, 2018))},
                    {'id': 2, 'label': '2017-2022', 'years': list(range(2017, 2023))}
                ]

                for period in periods:
                    period_expenditure_data = self.calculate_period_expenditure(
                        target_cities, settlement_dir, period['years'], period['id'], period['label']
                    )
                    if period_expenditure_data:
                        expenditure_data_list.append(period_expenditure_data)
                    else:
                        # データがない場合は空データを作成
                        empty_period_data = {
                            'year': period['id'],
                            'label': period['label'],
                            'per_capita_expenditure': '―',
                            'per_capita_expenditure_avg': '―',
                            'per_capita_expenditure_avg_delta': '―',
                            'per_capita_expenditure_delta_national_avg': '―',
                            'per_capita_expenditure_delta_pref_avg': '―',
                        }
                        expenditure_data_list.append(empty_period_data)

                # 一人あたり歳出額平均の変化を計算（直近5年間の平均 ÷ 過去5年間の平均）
                if len(expenditure_data_list) == 2:
                    past_period = expenditure_data_list[0]  # 2012-2017
                    recent_period = expenditure_data_list[1]  # 2017-2022

                    if (isinstance(past_period['per_capita_expenditure_avg'], (int, float)) and
                        isinstance(recent_period['per_capita_expenditure_avg'], (int, float)) and
                        past_period['per_capita_expenditure_avg'] > 0):

                        delta = recent_period['per_capita_expenditure_avg'] / past_period['per_capita_expenditure_avg']
                        recent_period['per_capita_expenditure_avg_delta'] = self.round_or_na(delta, 3)
            else:
                # ディレクトリが存在しない場合は期間別の空データを作成
                periods = [
                    {'id': 1, 'label': '2012-2017'},
                    {'id': 2, 'label': '2017-2022'}
                ]
                for period in periods:
                    empty_period_data = {
                        'year': period['id'],
                        'label': period['label'],
                        'per_capita_expenditure': '―',
                        'per_capita_expenditure_avg': '―',
                        'per_capita_expenditure_avg_delta': '―',
                        'per_capita_expenditure_delta_national_avg': '―',
                        'per_capita_expenditure_delta_pref_avg': '―',
                    }
                    expenditure_data_list.append(empty_period_data)

            # ファイル分離してエクスポート
            self.export(
                os.path.join(self.output_folder, f'IF106_財政関連評価指標_固定資産税ファイル{self.file_suffix}.csv'),
                fixed_asset_data_list,
            )

            self.export(
                os.path.join(self.output_folder, f'IF106_財政関連評価指標_歳出額ファイル{self.file_suffix}.csv'),
                expenditure_data_list,
            )

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

    def calculate_land_tax_data(self, target_cities):
        """固定資産税(土地)関連データを計算"""
        try:
            # 固定資産の価格等の概要調書パス
            fixed_asset_dir = os.path.join(self.input_folder, "25_固定資産の価格等の概要調書")

            # 利用可能な全年度のデータを取得
            annual_tax_data = {}

            if not os.path.exists(fixed_asset_dir):
                return {'latest_year': '―', 'latest_tax': '―', 'change_rate': '―'}

            # 年度フォルダを検索
            for folder_name in os.listdir(fixed_asset_dir):
                folder_path = os.path.join(fixed_asset_dir, folder_name)
                if os.path.isdir(folder_path) and "年度" in folder_name:
                    data = self.read_fixed_asset_data(fixed_asset_dir, folder_name)
                    if data is not None:
                        # 年度を抽出（例：2015年度 -> 2015）
                        try:
                            year = int(folder_name.replace("年度", ""))
                            annual_tax_data[year] = data
                        except ValueError:
                            continue

            if len(annual_tax_data) < 1:
                return {'latest_year': '―', 'latest_tax': '―', 'change_rate': '―'}

            # 各年度の固定資産税収を計算（万円）
            tax_by_year = {}
            for year, data in annual_tax_data.items():
                total_tax = 0
                for city_info in target_cities:
                    tax_revenue = self.extract_tax_base_amount(data, city_info, year)
                    if tax_revenue:
                        total_tax += tax_revenue

                if total_tax > 0:
                    tax_by_year[year] = total_tax

            if len(tax_by_year) == 0:
                return {'latest_year': '―', 'latest_tax': '―', 'change_rate': '―'}

            # 最新年度のデータを取得
            sorted_years = sorted(tax_by_year.keys())
            latest_year = sorted_years[-1]
            latest_tax = tax_by_year[latest_year]

            # 変化率を計算（2年度以上ある場合）
            change_rate = '―'
            if len(sorted_years) >= 2:
                earliest_year = sorted_years[0]
                earliest_tax = tax_by_year[earliest_year]

                if earliest_tax > 0:
                    change_rate = self.round_or_na(latest_tax / earliest_tax, 3)

            result = {
                'latest_year': latest_year,
                'latest_tax': self.round_or_na(latest_tax, 3),
                'change_rate': change_rate
            }

            return result

        except Exception as e:
            QgsMessageLog.logMessage(
                self.tr("Error in fixed asset tax calculation: %1").replace("%1", str(e)),
                self.tr("Plugin"),
                Qgis.Warning,
            )
            return {'latest_year': '―', 'latest_tax': '―', 'change_rate': '―'}

    def read_fixed_asset_data(self, base_dir, year_folder):
        """固定資産の価格等の概要調書を読み込み"""
        try:
            year_dir = os.path.join(base_dir, year_folder)
            if not os.path.exists(year_dir):
                return None

            # Excelファイルを検索
            for filename in os.listdir(year_dir):
                if filename.endswith(('.xlsx', '.xls')):
                    filepath = os.path.join(year_dir, filename)
                    # メインスレッドでExcelを読み込み
                    data = self.excel_reader.read_excel(filepath, None)
                    if data:
                        return data

            return None
        except Exception as e:
            QgsMessageLog.logMessage(
                self.tr("Error reading fixed asset data: %1").replace("%1", str(e)),
                self.tr("Plugin"),
                Qgis.Warning,
            )
            return None

    def extract_tax_base_amount(self, excel_data, city_info, year):
        """都道府県・市区町村名から課税標準額を抽出し、固定資産税収（万円）で計算"""
        try:
            if not excel_data:
                return 0

            prefecture_name = city_info['prefecture']
            name = city_info['name']

            # 年度に応じた列構造を設定
            # 2010年: B列（都道府県）、C列（市町村）、F列（合計）、T列（課税標準額）
            # 2015年: B列（都道府県）、C列（市町村）、F列（合計）、N列（課税標準額）
            # 2020年: B列（都道府県）、C列（市町村）、D列（合計）、M列（課税標準額）

            if year == 2010:
                total_col_idx = 5   # F列
                tax_col_idx = 19    # T列（課税標準額）
            elif year == 2015:
                total_col_idx = 5   # F列
                tax_col_idx = 13    # N列（課税標準額）
            elif year == 2020:
                total_col_idx = 3   # D列
                tax_col_idx = 12    # M列（課税標準額）
            else:
                # 未知の年度の場合はスキップ
                return 0

            # 全シートから該当市町村を検索
            for df in excel_data.values():
                if df is None or df.empty:
                    continue

                # 行ごとに検索
                for _, row in df.iterrows():
                    try:
                        # B列（index=1）とC列（index=2）で市町村を確認
                        if len(row) > 2:
                            pref_col = row.iloc[1] if hasattr(row, 'iloc') else row[1]
                            city_col = row.iloc[2] if hasattr(row, 'iloc') else row[2]

                            # 都道府県と市区町村名が一致するか確認
                            if (pd.notna(pref_col) and pd.notna(city_col) and
                                str(pref_col) == prefecture_name and str(city_col) == name):

                                # 年度に応じた「合計」列をチェック
                                is_total_row = False
                                if len(row) > total_col_idx:
                                    total_col = row.iloc[total_col_idx] if hasattr(row, 'iloc') else row[total_col_idx]
                                    if pd.notna(total_col) and str(total_col) == '合計':
                                        is_total_row = True

                                if is_total_row:
                                    # 課税標準額を取得
                                    tax_base = 0

                                    if len(row) > tax_col_idx:
                                        tax_col = row.iloc[tax_col_idx] if hasattr(row, 'iloc') else row[tax_col_idx]
                                        if pd.notna(tax_col) and isinstance(tax_col, (int, float)):
                                            tax_base = float(tax_col)

                                    # 固定資産税収を計算（万円）
                                    if tax_base > 0:
                                        tax_revenue = (tax_base * 0.014) / 10  # 千円→万円
                                        return tax_revenue
                    except:
                        continue

            return 0
        except Exception as e:
            QgsMessageLog.logMessage(
                self.tr("Error extracting tax base: %1").replace("%1", str(e)),
                self.tr("Plugin"),
                Qgis.Warning,
            )
            return 0

    def calculate_per_capita_data(self, target_cities):
        """一人当たり歳出額関連データを計算"""
        try:
            # 市町村別決算状況調パス
            settlement_dir = os.path.join(self.input_folder, "26_市町村別決算状況調")

            if not os.path.exists(settlement_dir):
                return {'latest_per_capita': '―', 'recent_avg_change': '―'}

            # 利用可能な全年度のデータを読み込み
            annual_data = {}

            for folder_name in os.listdir(settlement_dir):
                folder_path = os.path.join(settlement_dir, folder_name)
                if os.path.isdir(folder_path) and "年度" in folder_name:
                    try:
                        year = int(folder_name.replace("年度", ""))
                        data = self.read_settlement_data(settlement_dir, folder_name, year)
                        if data is not None:
                            annual_data[year] = data
                    except ValueError:
                        continue

            if len(annual_data) < 1:
                return {'latest_per_capita': '―', 'recent_avg_change': '―'}

            # 各年の一人当たり歳出額を計算
            per_capita_by_year = {}

            for year, data in annual_data.items():
                total_expenditure = 0
                total_population = 0

                for city_info in target_cities:
                    exp, pop = self.extract_expenditure_population(data, city_info)
                    total_expenditure += exp
                    total_population += pop


                if total_population > 0:
                    per_capita_by_year[year] = total_expenditure / total_population

            if len(per_capita_by_year) == 0:
                return {'latest_per_capita': '―', 'recent_avg_change': '―'}

            # 最新年度の一人当たり歳出額を取得
            sorted_years = sorted(per_capita_by_year.keys())
            latest_year = sorted_years[-1]
            latest_per_capita = per_capita_by_year[latest_year]

            # 対前年比増減率を計算（2年以上ある場合）
            recent_avg_change = '―'
            if len(sorted_years) >= 2:
                # 対前年比増減率を計算
                growth_rates = {}
                for i in range(1, len(sorted_years)):
                    prev_year = sorted_years[i-1]
                    curr_year = sorted_years[i]
                    if per_capita_by_year[prev_year] > 0:
                        growth_rate = (per_capita_by_year[curr_year] - per_capita_by_year[prev_year]) / per_capita_by_year[prev_year]
                        growth_rates[curr_year] = growth_rate

                # 取り込めたデータの最新年を基準に期間を設定
                available_years = sorted(growth_rates.keys())

                if len(available_years) > 0:
                    latest_growth_year = available_years[-1]

                    # 直近5年間：最新年から過去5年間
                    recent_start = latest_growth_year - 4
                    recent_end = latest_growth_year

                    # 過去5年間：直近5年間の前の5年間
                    past_start = recent_start - 5
                    past_end = recent_start - 1

                    # 実際に取り込めたデータのみで集計
                    past_rates = [growth_rates[y] for y in available_years
                                 if past_start <= y <= past_end and y in growth_rates]
                    recent_rates = [growth_rates[y] for y in available_years
                                   if recent_start <= y <= recent_end and y in growth_rates]

                    if len(past_rates) > 0 and len(recent_rates) > 0:
                        past_avg = sum(past_rates) / len(past_rates)
                        recent_avg = sum(recent_rates) / len(recent_rates)

                        if past_avg != 0:
                            change_ratio = recent_avg / past_avg
                            recent_avg_change = self.round_or_na(change_ratio, 3)

            return {
                'latest_per_capita': self.round_or_na(latest_per_capita, 0),
                'recent_avg_change': recent_avg_change
            }

        except Exception as e:
            QgsMessageLog.logMessage(
                self.tr("Error in per capita expenditure calculation: %1").replace("%1", str(e)),
                self.tr("Plugin"),
                Qgis.Warning,
            )
            return {'latest_per_capita': '―', 'recent_avg_change': '―'}

    def read_settlement_data(self, base_dir, year_folder, year):
        """市町村別決算状況調を読み込み"""
        try:
            year_dir = os.path.join(base_dir, year_folder)
            if not os.path.exists(year_dir):
                return None

            # Excelファイルを検索（一時ファイルを除外）
            for filename in os.listdir(year_dir):
                if (filename.endswith(('.xlsx', '.xls', '.XLS')) and
                    not filename.startswith('~$')):
                    filepath = os.path.join(year_dir, filename)
                    try:
                        # ファイル拡張子に応じてエンジンを指定
                        if filename.endswith('.xlsx'):
                            engine = 'openpyxl'
                        else:
                            engine = 'xlrd'

                        # メインスレッドでExcelを読み込み
                        data = self.excel_reader.read_excel(filepath, engine)
                        if data:
                            return data
                    except Exception as file_error:
                        QgsMessageLog.logMessage(
                            self.tr("Skipping file {0}: {1}").format(filename, str(file_error)),
                            self.tr("Plugin"),
                            Qgis.Warning,
                        )
                        continue

            return None
        except Exception as e:
            QgsMessageLog.logMessage(
                self.tr("Error reading settlement data: %1").replace("%1", str(e)),
                self.tr("Plugin"),
                Qgis.Warning,
            )
            return None

    def extract_expenditure_population(self, excel_data, city_info):
        """都道府県・市区町村名から歳出総額と人口を抽出"""
        try:
            if not excel_data:
                return 0, 0

            prefecture_name = city_info['prefecture']
            name = city_info['name']

            # 都道府県名をスペース区切りに変換（例：栃木県 → 栃　木　県）
            spaced_prefecture = '　'.join(list(prefecture_name))


            # 全シートから該当市町村を検索
            for df in excel_data.values():
                if df is None or df.empty:
                    continue

                # 2段階検索：1) 都道府県を探す 2) その後の市区町村を探す
                in_target_prefecture = False

                for row_idx, row in df.iterrows():
                    try:
                        # P列（index=15）をチェック
                        if len(row) > 15:
                            p_col = row.iloc[15] if hasattr(row, 'iloc') else row[15]
                            p_col_text = str(p_col).strip() if pd.notna(p_col) else ""

                            # 1段階目：都道府県を探す
                            if p_col_text == spaced_prefecture:
                                in_target_prefecture = True
                                continue

                            # 都道府県内でない場合はスキップ
                            if not in_target_prefecture:
                                continue

                            # 「合　　　計」が来たら都道府県セクション終了
                            if "合" in p_col_text and "計" in p_col_text:
                                in_target_prefecture = False
                                continue

                            # 2段階目：目標市区町村を探す
                            if p_col_text == name:

                                # 人口と歳出総額を列インデックスで取得
                                # S列（人口）= index 18
                                # AO列（歳出総額）= index 40
                                population = 0
                                expenditure = 0

                                # S列から人口を取得
                                if len(row) > 18:
                                    pop_col = row.iloc[18] if hasattr(row, 'iloc') else row[18]
                                    if pd.notna(pop_col) and isinstance(pop_col, (int, float)):
                                        population = float(pop_col)

                                # AO列から歳出総額を取得
                                if len(row) > 40:
                                    exp_col = row.iloc[40] if hasattr(row, 'iloc') else row[40]
                                    if pd.notna(exp_col) and isinstance(exp_col, (int, float)):
                                        expenditure = float(exp_col)

                                return expenditure, population
                    except:
                        continue

            return 0, 0
        except Exception as e:
            QgsMessageLog.logMessage(
                self.tr("Error extracting expenditure/population: %1").replace("%1", str(e)),
                self.tr("Plugin"),
                Qgis.Warning,
            )
            return 0, 0

    def export_empty_files(self):
        """空のデータを2つのファイルに出力"""
        # 固定資産税ファイル用の空データ
        empty_fixed_asset_data = {
            # 年次
            'year': '―',
            # 固定資産税(土地)
            'land_fixed_asset_tax': '―',
            # 固定資産税(土地)の変化率
            'land_fixed_asset_tax_change_rate': '―',
            # 固定資産税(土地)の変化率の変化
            'land_fixed_asset_tax_change_rate_delta': '―',
            # 全国平均値
            'land_fixed_asset_tax_revenue_national_avg': '―',
            # 都道府県平均値
            'land_fixed_asset_tax_revenue_pref_avg': '―',
        }

        # 歳出額ファイル用の空データ
        empty_expenditure_data_list = [
            {
                # 期間ID
                'year': 1,
                # 期間ラベル
                'label': '2012-2017',
                # 1人当たりの歳出額
                'per_capita_expenditure': '―',
                # 一人あたり歳出額平均
                'per_capita_expenditure_avg': '―',
                # 一人あたり歳出額平均の変化
                'per_capita_expenditure_avg_delta': '―',
                # 全国平均値
                'per_capita_expenditure_delta_national_avg': '―',
                # 都道府県平均値
                'per_capita_expenditure_delta_pref_avg': '―',
            },
            {
                # 期間ID
                'year': 2,
                # 期間ラベル
                'label': '2017-2022',
                # 1人当たりの歳出額
                'per_capita_expenditure': '―',
                # 一人あたり歳出額平均
                'per_capita_expenditure_avg': '―',
                # 一人あたり歳出額平均の変化
                'per_capita_expenditure_avg_delta': '―',
                # 全国平均値
                'per_capita_expenditure_delta_national_avg': '―',
                # 都道府県平均値
                'per_capita_expenditure_delta_pref_avg': '―',
            }
        ]

        # 2つのファイルに分離してエクスポート
        self.export(
            os.path.join(self.output_folder, f'IF106_財政関連評価指標_固定資産税ファイル{self.file_suffix}.csv'),
            [empty_fixed_asset_data],
        )

        self.export(
            os.path.join(self.output_folder, f'IF106_財政関連評価指標_歳出額ファイル{self.file_suffix}.csv'),
            empty_expenditure_data_list,
        )

    def calculate_period_expenditure(self, target_cities, settlement_dir, target_years, period_id, period_label):
        """固定期間の歳出額データを計算"""
        try:
            # 対象年度のデータを読み込み
            annual_data = {}
            for year in target_years:
                year_folder = f"{year}年度"
                data = self.read_settlement_data(settlement_dir, year_folder, year)
                if data is not None:
                    annual_data[year] = data

            if len(annual_data) < 1:
                return None

            # 各年の一人当たり歳出額を計算
            per_capita_by_year = {}
            for year, data in annual_data.items():
                total_expenditure = 0
                total_population = 0

                for city_info in target_cities:
                    exp, pop = self.extract_expenditure_population(data, city_info)
                    total_expenditure += exp
                    total_population += pop

                if total_population > 0:
                    per_capita_by_year[year] = total_expenditure / total_population

            if len(per_capita_by_year) < 1:
                return None

            # 期間内の一人当たり歳出額の平均値を計算
            avg_per_capita = '―'
            if len(per_capita_by_year) > 0:
                avg_per_capita = self.round_or_na(sum(per_capita_by_year.values()) / len(per_capita_by_year), 1)

            # 期間内の一人当たり歳出額の合計を計算
            total_per_capita = '―'
            if len(per_capita_by_year) > 0:
                total_per_capita = self.round_or_na(sum(per_capita_by_year.values()), 0)


            return {
                # 期間ID（連番）
                'year': period_id,
                # 期間ラベル
                'label': period_label,
                # 1人当たりの歳出額（期間内合計）
                'per_capita_expenditure': total_per_capita,
                # 一人あたり歳出額平均
                'per_capita_expenditure_avg': avg_per_capita,
                # 一人あたり歳出額平均の変化
                'per_capita_expenditure_avg_delta': '―',
                # 全国平均値
                'per_capita_expenditure_delta_national_avg': '―',
                # 都道府県平均値
                'per_capita_expenditure_delta_pref_avg': '―',
            }

        except Exception as e:
            QgsMessageLog.logMessage(
                self.tr("Error in period expenditure calculation: %1").replace("%1", str(e)),
                self.tr("Plugin"),
                Qgis.Warning,
            )
            return None
