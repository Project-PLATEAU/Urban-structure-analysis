"""
/***************************************************************************
 *                                                                         
 * Population モデル
 * e-Stat(https://www.e-stat.go.jp/gis/statmap-search?page=1&type=1&toukeiCode=00200521)
 * からダウンロードする国勢調査人口データ
 * ※年度毎にフォーマットが異なる
 *                                                                         
 ***************************************************************************/
"""
from qgis.core import QgsMessageLog, Qgis
class PopulationModel:
    # 属性定義
    attributes = [
        # メッシュコード
        "key_code", 
        # 人口総数
        "population", 
        # 男性人口
        "male", 
        # 女性人口
        "female", 
        # 0〜14歳人口 総数
        "age_0_14",  
        # 0〜14歳人口 男
        "age_0_14_male",  
        # 0〜14歳人口 女
        "age_0_14_female",  
        # 15歳以上人口 総数
        "age_15_total",  
        # 15歳以上人口 男
        "age_15_male",  
        # 15歳以上人口 女
        "age_15_female",  
        # 15〜64歳人口 総数
        "age_15_64",  
        # 15〜64歳人口 男
        "age_15_64_male",  
        # 15〜64歳人口 女
        "age_15_64_female",  
        # 65歳以上人口 総数
        "age_65_",  
        # 65歳以上人口 男
        "age_65_male",  
        # 65歳以上人口 女
        "age_65_female",  
        # 75歳以上人口 総数
        "age_75_total",  
        # 75歳以上人口 男
        "age_75_male",  
        # 75歳以上人口 女
        "age_75_female",  
        # 85歳以上人口 総数
        "age_85_total",  
        # 85歳以上人口 男
        "age_85_male",  
        # 85歳以上人口 女
        "age_85_female",  
        # 95歳以上人口 総数
        "age_95_total",  
        # 95歳以上人口 男
        "age_95_male",  
        # 95歳以上人口 女
        "age_95_female",  
    ]

    # 各年度の列マッピング
    year_mappings = {
        2010: {
            # メッシュコード
            "KEY_CODE": "key_code",
            # 人口総数
            "T000649001": "population", 
            # 男性人口
            "T000649002": "male",  
            # 女性人口
            "T000649003": "female",  
            # 世帯総数
            "T000649004": "households",  
        },
        2015: {
            # メッシュコード
            "KEY_CODE": "key_code",  
            # 人口総数
            "T000876001": "population",  
            # 男性人口
            "T000876002": "male",  
            # 女性人口
            "T000876003": "female",  
            # 0〜14歳人口 総数
            "T000876004": "age_0_14",  
            # 0〜14歳人口 男
            "T000876005": "age_0_14_male",  
            # 0〜14歳人口 女
            "T000876006": "age_0_14_female",  
            # 15歳以上人口 総数
            "T000876007": "age_15_total",  
            # 15歳以上人口 男
            "T000876008": "age_15_male",  
            # 15歳以上人口 女
            "T000876009": "age_15_female",  
            # 15〜64歳人口 総数
            "T000876010": "age_15_64",  
            # 15〜64歳人口 男
            "T000876011": "age_15_64_male",  
            # 15〜64歳人口 女
            "T000876012": "age_15_64_female",  
            # 20歳以上人口 総数
            "T000876013": "age_20_total",  
            # 20歳以上人口 男
            "T000876014": "age_20_male",  
            # 20歳以上人口 女
            "T000876015": "age_20_female",  
            # 65歳以上人口 総数
            "T000876016": "age_65_",  
            # 65歳以上人口 男
            "T000876017": "age_65_male",  
            # 65歳以上人口 女
            "T000876018": "age_65_female",  
            # 75歳以上人口 総数
            "T000876019": "age_75_total",  
            # 75歳以上人口 男
            "T000876020": "age_75_male",  
            # 75歳以上人口 女
            "T000876021": "age_75_female",  
            # 外国人人口 総数
            "T000876022": "foreign_population_total",  
            # 外国人男性人口
            "T000876023": "foreign_population_male",  
            # 外国人女性人口
            "T000876024": "foreign_population_female",  
            # 世帯総数
            "T000876025": "households",  
            # 一般世帯数
            "T000876026": "general_households",  
            # 1人世帯数
            "T000876027": "one_person_households",  
            # 2人世帯数
            "T000876028": "two_person_households",  
            # 3人世帯数
            "T000876029": "three_person_households",  
            # 4人世帯数
            "T000876030": "four_person_households",  
            # 5人世帯数
            "T000876031": "five_person_households",  
            # 6人世帯数
            "T000876032": "six_person_households",  
            # 7人以上世帯数
            "T000876033": "seven_person_households",  
            # 親族のみ世帯数
            "T000876034": "relatives_households",  
            # 核家族世帯数
            "T000876035": "nuclear_family_households",  
            # 核家族以外世帯数
            "T000876036": "non_nuclear_households",  
            # 6歳未満世帯員のいる世帯数
            "T000876037": "households_with_under_6",  
            # 65歳以上世帯員のいる世帯数
            "T000876038": "households_with_65_up",  
            # 世帯主が20～29歳の1人世帯数
            "T000876039": "young_head_one_person",  
            # 高齢単身世帯数
            "T000876040": "elderly_one_person",  
            # 高齢夫婦のみ世帯数
            "T000876041": "elderly_couple",  
        },
        2020: {
            # メッシュコード
            "KEY_CODE": "key_code",  
            # 人口総数
            "T001142001": "population",  
            # 男性人口
            "T001142002": "male",  
            # 女性人口
            "T001142003": "female",  
            # 0〜14歳人口 総数
            "T001142004": "age_0_14",  
            # 0〜14歳人口 男
            "T001142005": "age_0_14_male",  
            # 0〜14歳人口 女
            "T001142006": "age_0_14_female",  
            # 15歳以上人口 総数
            "T001142007": "age_15_total",  
            # 15歳以上人口 男
            "T001142008": "age_15_male",  
            # 15歳以上人口 女
            "T001142009": "age_15_female",  
            # 15〜64歳人口 総数
            "T001142010": "age_15_64",  
            # 15〜64歳人口 男
            "T001142011": "age_15_64_male",  
            # 15〜64歳人口 女
            "T001142012": "age_15_64_female",  
            # 65歳以上人口 総数
            "T001142019": "age_65_",  
            # 65歳以上人口 男
            "T001142020": "age_65_male",  
            # 65歳以上人口 女
            "T001142021": "age_65_female",  
            # 75歳以上人口 総数
            "T001142022": "age_75_total",  
            # 75歳以上人口 男
            "T001142023": "age_75_male",  
            # 75歳以上人口 女
            "T001142024": "age_75_female",  
            # 85歳以上人口 総数
            "T001142025": "age_85_total",  
            # 85歳以上人口 男
            "T001142026": "age_85_male",  
            # 85歳以上人口 女
            "T001142027": "age_85_female",  
            # 95歳以上人口 総数
            "T001142028": "age_95_total",  
            # 95歳以上人口 男
            "T001142029": "age_95_male",  
            # 95歳以上人口 女
            "T001142030": "age_95_female",  
            # 外国人人口 総数
            "T001142031": "foreign_population_total",  
            # 外国人男性人口
            "T001142032": "foreign_population_male",  
            # 外国人女性人口
            "T001142033": "foreign_population_female",  
            # 世帯総数
            "T001142034": "households",  
            # 一般世帯数
            "T001142035": "general_households",  
            # 1人世帯数
            "T001142036": "one_person_households",  
            # 2人世帯数
            "T001142037": "two_person_households",  
            # 3人世帯数
            "T001142038": "three_person_households",  
            # 4人世帯数
            "T001142039": "four_person_households",  
            # 5人世帯数
            "T001142040": "five_person_households",  
            # 6人世帯数
            "T001142041": "six_person_households",  
            # 7人以上世帯数
            "T001142042": "seven_person_households",  
            # 親族のみ世帯数
            "T001142043": "relatives_households",  
            # 核家族世帯数
            "T001142044": "nuclear_family_households",  
            # 核家族以外世帯数
            "T001142045": "non_nuclear_households",  
            # 6歳未満世帯員のいる世帯数
            "T001142046": "households_with_under_6",  
            # 65歳以上世帯員のいる世帯数
            "T001142047": "households_with_65_up",  
            # 世帯主が20～29歳の1人世帯数
            "T001142048": "young_head_one_person",  
            # 高齢単身世帯数
            "T001142049": "elderly_one_person",  
            # 高齢夫婦のみ世帯数
            "T001142050": "elderly_couple",  
        }
    }



    @staticmethod
    def parse(year, data):
        """データをマッピングに基づいてパース"""
        mapping = PopulationModel.year_mappings.get(year, {})
        parsed_data = {}

        # マッピングに基づいてパース
        for col, value in data.items():
            attribute = mapping.get(col)
            if attribute:
                parsed_data[attribute] = value

        return parsed_data

