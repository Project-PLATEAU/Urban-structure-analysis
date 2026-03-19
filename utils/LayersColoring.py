"""
/***************************************************************************
 LayersColoring
                                 A QGIS plugin module
 Used for layer color styling based on XML configuration
                              -------------------
        begin                : 2024-08-29
        git sha              : $Format:%H$
        copyright            : (C) 2024 by Author
        email                : mail
 ***************************************************************************/


/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""
import os
import re
import tempfile
import xml.etree.ElementTree as ET
import matplotlib.pyplot as plt

from PyQt5.QtGui import QColor # pylint: disable=import-error, no-name-in-module
from qgis.core import (QgsProject, QgsFillSymbol, QgsLineSymbol, # pylint: disable=import-error
                       QgsSymbol, QgsMarkerSymbol,
                       QgsUnitTypes, QgsSingleSymbolRenderer,
                       QgsLinePatternFillSymbolLayer, QgsRendererCategory,
                       QgsGraduatedSymbolRenderer, QgsRendererRange,
                       QgsSimpleFillSymbolLayer, QgsRuleBasedRenderer,
                       QgsCategorizedSymbolRenderer, QgsSimpleLineSymbolLayer)
from qgis.PyQt.QtCore import Qt # pylint: disable=import-error
from qgis.utils import iface # pylint: disable=import-error

plt.rcParams['font.family'] = "MS Gothic"
_config_dir = os.path.join(os.path.dirname(
    os.path.abspath(__file__)), '../config'
)
_config_file = os.path.join(_config_dir, 'LayersColoringConfig.xml')
_metric_config_file = os.path.join(_config_dir, 'MetricCalculationConfig.xml')

_qml_dir = os.path.join(os.path.dirname(
    os.path.abspath(__file__)), '../qml_files'
)

_datalist_config_file = os.path.join(_config_dir, 'DataListConfig.xml')
_qml_directory_config_file = os.path.join(_config_dir, 'QmlFilesDirectoryConfig.xml')

def safe_find(element, tag, default=''):
    """
    XML要素を安全に検索し、見つからない場合はデフォルト値を返す関数

    :param element: XMLの親要素
    :type element: xml.etree.ElementTree.Element
    :param tag: 検索するタグ名
    :type tag: str
    :param default: 見つからない場合に返すデフォルト値（デフォルトは空文字列）
    :type default: str

    :return: 検索されたタグの値、またはデフォルト値
    :rtype: str
    """
    found = element.find(tag)
    return found.text if found is not None else default

class LayersColoring:
    """
    XML設定に基づいてQGISレイヤーにカラースタイリングを適用するクラス

    属性:
        iface (QgisInterface): QGISインターフェイスオブジェクト。
        layer (QgsLayer): 現在アクティブなQGISレイヤー。
        layer_config (dict): レイヤー情報を含む設定ファイルの内容。

    メソッド:
        __init__(self): クラスを初期化し、アクティブレイヤーを設定する。
        load_layer_config(self): XML設定ファイルを読み込み、レイヤー情報を取得する。
        apply_single_style(self, layer_info): 単一定義用のシンボルを設定する。
        apply_categorized_style(self, layer_info): カテゴリ値定義用のシンボルを設定する。
        apply_graduated_style(self, layer_info): graduated値の定義に基づいてシンボルを設定する。
        apply_ruled_style(self, layer_info): ルールに基づいてシンボルを設定する。
        parse_color(self, color_str): 'r,g,b,a'形式の文字列をQColorオブジェクトに変換する。
        hashed_layer(self, data, hashed): 斜線模様のフィルパターンを持つシンボルを設定する。
        coloring(self, item_val, year): アイテムの値と年に基づいて地図レイヤーに色を付ける操作を実行する。
    """
    def __init__(self):
        self.iface = iface
        self.layer = self.iface.activeLayer()
        self.layer_config = self.load_layer_config()

        self.datalist_config = self.load_datalist_config()
        self.qml_base_folder = self.load_qml_directory_config()

    def load_layer_config(self):
        """
        XML設定ファイルを読み取り、レイヤー情報を取得する関数

        :return: レイヤー設定の辞書
        :rtype: dict
        """
        metric_tree = ET.parse(_metric_config_file)
        metric_root = metric_tree.getroot()
        elem = metric_root.find('threshold_bus')
        threshold_bus = elem.text

        tree = ET.parse(_config_file)
        root = tree.getroot()

        datasets = {}
        for dataset in root.find('datasets'):
            item_val = dataset.find('item_val').text
            year = dataset.find('year').text
            layers = []

            for layer in dataset.find('layerlist'):
                layer_info = {
                    'name': layer.find('name').text,
                    'geometryType': layer.find('geometryType').text,
                    'type': layer.find('type').text,
                    'column': safe_find(layer, 'column', ''),
                    'layerNo': safe_find(layer, 'z-index', ''),
                    'data': [],
                    'scale-visibility': safe_find(
                        layer, 'scale-visibility', 'false'
                    ),
                    'scale-max': safe_find(layer, 'scale-max', ''),
                    'scale-min': safe_find(layer, 'scale-min', ''),
                    'enableSymbolLevels': safe_find(
                        layer, 'enableSymbolLevels', 'false'
                    )
                }

                for data in layer.find('datalist'):
                    data_info = {}
                    if layer_info['type'] == 'categorized':
                        value = data.find('value').text
                        if value == 'threshold_bus':
                            value = threshold_bus
                        data_info['value'] = value
                        data_info['label_name'] = safe_find(
                            data, 'label_name', ''
                        )
                        data_info['renderPass'] = safe_find(data, 'renderPass', '0')
                    elif layer_info['type'] == 'graduated':
                        data_info['upperthreshold'] = (
                            data.find('upperthreshold').text
                        )
                        data_info['underthreshold'] = (
                            data.find('underthreshold').text
                        )
                        data_info['label_name'] = safe_find(data,
                                                             'label_name',
                                                             '')
                    elif layer_info['type'] == 'ruled':
                        data_info['value'] = data.find('value').text
                        data_info['rule'] = data.find('rule').text
                    elif layer_info['type'] == 'single':
                        pass
                    else:
                        continue

                    data_info['borderStyle'] = data.find('borderStyle').text
                    data_info['borderColor'] = data.find('borderColor').text
                    data_info['fillColor'] = safe_find(
                        data, 'fillColor', 'default_fill_color'
                    )
                    data_info['fillPattern'] = safe_find(
                        data, 'fillPattern', 'default_fill_pattern'
                    )
                    data_info['fillPattern_interval'] = safe_find(
                        data, 'fillPattern_interval', '5.0'
                    )
                    data_info['lineWidth'] = safe_find(data,'lineWidth', '1.0')
                    data_info['size'] = safe_find(data, 'size', '1.0')
                    data_info['opacity'] = safe_find(layer, 'opacity', '')

                    layer_info['data'].append(data_info)
                layers.append(layer_info)

            if item_val not in datasets:
                datasets[item_val] = {}
            datasets[item_val][year] = layers
        return datasets

    def load_qml_directory_config(self):
        """
        QmlFilesDirectoryConfig.xmlからQMLファイルのベースフォルダパスを読み込む

        Summary:
            QmlFilesDirectoryConfig.xmlの<folder>要素からパスを取得し、
            見つからない場合は従来のqml_filesディレクトリを使用する

        :return: QMLファイルのベースフォルダパス
        :rtype: str
        """
        try:
            if os.path.exists(_qml_directory_config_file):
                tree = ET.parse(_qml_directory_config_file)
                root = tree.getroot()
                folder_element = root.find('folder')
                if folder_element is not None and folder_element.text:
                    base_path = folder_element.text.strip()
                    print(f"QMLベースフォルダを設定: {base_path}")
                    return base_path
        except Exception as e:
            print(f"QmlFilesDirectoryConfig.xml読み込みエラー: {str(e)}")

        # デフォルトのqml_filesディレクトリを返す
        print(f"デフォルトQMLディレクトリを使用: {_qml_dir}")
        return _qml_dir

    def load_datalist_config(self):
        """
        DataListConfig.xmlから評価指標のマッピング情報を読み込む

        Summary:
            DataListConfig.xmlからitem_valと評価指標名のマッピングを構築し、
            フォルダ構造でのパス構築に使用する辞書を作成する

        :return: 評価指標のマッピング辞書
        :rtype: dict
        """
        mapping = {}

        try:
            if os.path.exists(_datalist_config_file):
                tree = ET.parse(_datalist_config_file)
                root = tree.getroot()

                data_items = root.find('data_items')
                if data_items is not None:
                    for item in data_items.findall('item'):
                        category_label = item.find('label')
                        if category_label is not None:
                            category_name = category_label.text

                            sub_items = item.find('sub_items')
                            if sub_items is not None:
                                for sub_item in sub_items.findall('sub_item'):
                                    label_elem = sub_item.find('label')
                                    value_elem = sub_item.find('value')

                                    if label_elem is not None and value_elem is not None:
                                        label_text = label_elem.text
                                        item_val = value_elem.text

                                        # 空ラベルはスキップ
                                        if label_text and label_text.strip():
                                            mapping[item_val] = {
                                                'category': category_name,
                                                'subcategory': label_text.strip()
                                            }

                print(f"DataList設定を読み込み: {len(mapping)}件のマッピング")
                return mapping

        except Exception as e:
            print(f"DataListConfig.xml読み込みエラー: {str(e)}")

        return {}

    def construct_qml_path_from_structure(self, layer_info, item_val):
        """
        フォルダ構造に基づいてQMLファイルパスを構築する

        Summary:
            DataListConfig.xmlの情報を使用して、以下の構造でQMLファイルパスを構築する:
            qml_base_folder/カテゴリ名/サブカテゴリ名/レイヤー名.qml

        :param layer_info: レイヤー情報辞書
        :type layer_info: dict
        :param item_val: アイテム値（DataListConfig.xmlのvalue）
        :type item_val: str

        :return: 構築されたQMLファイルパス（存在する場合）、存在しない場合はNone
        :rtype: str or None
        """
        # item_valから評価指標情報を取得
        if item_val not in self.datalist_config:
            print(f"item_val {item_val} がDataListConfigに見つかりません")
            return None

        category = self.datalist_config[item_val]['category']
        subcategory = self.datalist_config[item_val]['subcategory']
        layer_name = layer_info.get('name', '')

        if not all([category, subcategory, layer_name]):
            print(f"必要な情報が不足: category={category}, subcategory={subcategory}, layer_name={layer_name}")
            return None

        # QMLファイル名を構築: レイヤー名.qml
        qml_filename = f"{layer_name}.qml"

        # フルパスを構築
        full_path = os.path.join(
            self.qml_base_folder,
            category,
            subcategory,
            qml_filename
        )

        # ファイルの存在確認
        if os.path.exists(full_path):
            return full_path
        else:
            return None

    def apply_single_style(self, layer_info):
        """
        単一定義用のシンボルを設定する関数

        :param layer_info: レイヤー情報を含む辞書
        :type layer_info: dict
        """
        SingleSymbol = []
        for data in layer_info['data']:
            if layer_info['geometryType'] == 'polygon':
                if data['fillPattern'] == 'Bhashed':
                    symbol = self.hashed_layer(data, data['fillPattern'])
                elif data['fillPattern'] == 'Fhashed':
                    symbol = self.hashed_layer(data, data['fillPattern'])
                else:
                    symbol = QgsFillSymbol.createSimple({
                        'color': data['fillColor'],
                        'outline_color': data['borderColor'],
                        'outline_style': data['borderStyle'],
                        'outline_width': data['lineWidth'],
                        "outline_width_unit": "POINTS",
                    })

                if data['opacity'] != '':
                    try:
                        opacity = float(data['opacity'])
                        symbol.setOpacity(opacity)
                    except ValueError:
                        pass
            elif layer_info['geometryType'] == 'line':
                if data['borderStyle'] == 'jr':
                    symbol = QgsLineSymbol()

                    simple_line_symbol_layer = QgsSimpleLineSymbolLayer()
                    simple_line_symbol_layer.setColor(QColor('black'))
                    simple_line_symbol_layer.setWidth(1.26)

                    dash_line_symbol_layer = QgsSimpleLineSymbolLayer()
                    dash_line_symbol_layer.setColor(QColor('white'))
                    dash_line_symbol_layer.setWidth(0.66)
                    dash_line_symbol_layer.setUseCustomDashPattern(True)
                    dash_line_symbol_layer.setPenStyle(Qt.CustomDashLine)
                    dash_line_symbol_layer.setCustomDashVector([3, 3])
                    symbol.changeSymbolLayer(0, simple_line_symbol_layer)
                    symbol.appendSymbolLayer(dash_line_symbol_layer)
                else:
                    symbol = QgsLineSymbol.createSimple({
                        'color': data['borderColor'],
                        'line_style': data['borderStyle'],
                        'width': data['lineWidth']
                    })
            elif layer_info['geometryType'] == 'point':
                symbol = QgsMarkerSymbol.createSimple({
                    'size': data['size'],
                    "size_unit": "POINTS",
                    'color': data['fillColor'],
                    'line_color': data['borderColor'],
                    'line_width': data['lineWidth'],
                    'line_width_unit': 'POINTS'
                })
            else:
                return

            SingleSymbol.append(symbol)

        renderer = QgsSingleSymbolRenderer(SingleSymbol[0])
        if renderer is not None:
            self.layer.setRenderer(renderer)
            self.layer.triggerRepaint()

    def apply_categorized_style(self, layer_info):
        """
        カテゴリ値定義用のシンボルを設定する関数

        :param layer_info: レイヤー情報を含む辞書
        :type layer_info: dict
        """
        categories = []
        for data in layer_info['data']:
            if layer_info['geometryType'] == 'polygon':
                if data['fillPattern'] == 'Bhashed':
                    symbol = self.hashed_layer(data, data['fillPattern'])
                elif data['fillPattern'] == 'Fhashed':
                    symbol = self.hashed_layer(data, data['fillPattern'])
                else:
                    symbol = QgsFillSymbol.createSimple({
                        'color': data['fillColor'],
                        'outline_color': data['borderColor'],
                        'outline_style': data['borderStyle'],
                        'outline_width': data['lineWidth'],
                        "outline_width_unit": "POINTS",
                    })

                if data['opacity'] != '':
                    try:
                        opacity = float(data['opacity'])
                        symbol.setOpacity(opacity)
                    except ValueError:
                        pass

                if 'renderPass' != '':
                    try:
                        render_pass = int(data['renderPass'])
                        for symbol_layer in symbol.symbolLayers():
                            symbol_layer.setRenderingPass(render_pass)
                    except (ValueError, TypeError):
                        pass

            elif layer_info['geometryType'] == 'line':
                symbol = QgsLineSymbol.createSimple({
                    'color': data['borderColor'],
                    'line_style': data['borderStyle'],
                    'width': data['lineWidth'],
                    "width_unit": "POINTS",
                })
            elif layer_info['geometryType'] == 'point':
                symbol = QgsMarkerSymbol.createSimple({
                    'size': data['size'],
                    "size_unit": "POINTS",
                    'color': data['fillColor'],
                    'line_color': data['borderColor'],
                    'line_width': data['lineWidth'],
                    'line_width_unit': 'POINTS'
                })
            else:
                return

            category = QgsRendererCategory(data['value'], symbol, data['value'])
            category.setLabel(data['label_name'])
            categories.append(category)

        renderer = QgsCategorizedSymbolRenderer(
            layer_info['column'], categories
        )

        if layer_info['enableSymbolLevels'] == 'true':
            renderer.setUsingSymbolLevels(True)

        if renderer is not None:
            self.layer.setRenderer(renderer)
            self.layer.triggerRepaint()

    def apply_graduated_style(self, layer_info):
        """
        graduated値の定義に基づいてシンボルを設定する関数

        :param layer_info: レイヤー情報を含む辞書
        :type layer_info: dict
        """
        if self.layer is None:
            print("Layer not found")
            return

        column = layer_info['column']
        ranges = []

        for data in layer_info['data']:
            lower = float(data['underthreshold'])
            upper = float(data['upperthreshold'])
            fill_color = data.get('fillColor')

            if fill_color is not None:
                fill_color = fill_color.strip()
            else:
                fill_color = ''

            if layer_info['geometryType'] == 'polygon':
                setting_symbol = QgsFillSymbol.createSimple({
                    'color': fill_color,
                    'outline_color': data['borderColor'],
                    'outline_style': data['borderStyle'],
                    'outline_width': data['lineWidth'],
                    "outline_width_unit": "POINTS",
                })

                if data['opacity'] != '':
                    try:
                        opacity = float(data['opacity'])
                        setting_symbol.setOpacity(opacity)
                    except ValueError:
                        pass
            elif layer_info['geometryType'] == 'line':
                setting_symbol = QgsLineSymbol.createSimple({
                    'color': data['borderColor'],
                    'line_style': data['borderStyle'],
                    'width': data['lineWidth'],
                    "width_unit": "POINTS",
                })

            elif layer_info['geometryType'] == 'point':
                setting_symbol = QgsMarkerSymbol.createSimple({
                    'size': data['size'],
                    "size_unit": "POINTS",
                    'color': fill_color,
                    'line_color': data['borderColor'],
                    'line_width': data['lineWidth'],
                    'line_width_unit': 'POINTS'
                })
            else:
                return

            range_ = QgsRendererRange(
                lower, upper, setting_symbol, f"{lower} - {upper}"
            )
            if data['label_name'] != '':
                range_.setLabel(data['label_name'])
            ranges.append(range_)

            renderer = QgsGraduatedSymbolRenderer(column, ranges)

        self.layer.setRenderer(renderer)
        self.layer.triggerRepaint()

    def apply_ruled_style(self, layer_info):
        """
        ルールに基づいてシンボルを設定する関数

        :param layer_info: レイヤー情報を含む辞書
        :type layer_info: dict
        """
        symbol = QgsSymbol.defaultSymbol(self.layer.geometryType())
        renderer = QgsRuleBasedRenderer(symbol)
        root_rule = renderer.rootRule()

        for data in layer_info['data']:
            try:
                rule = root_rule.children()[0].clone()

                if layer_info['geometryType'] == 'polygon':
                    if data['fillPattern'] == 'Bhashed':
                        symbol = self.hashed_layer(data, data['fillPattern'])
                    elif data['fillPattern'] == 'Fhashed':
                        symbol = self.hashed_layer(data, data['fillPattern'])
                    else:
                        setting_symbol = QgsFillSymbol.createSimple({
                            'color': data['fillColor'],
                            'outline_color': data['borderColor'],
                            'outline_style': data['borderStyle'],
                            'outline_width': data['lineWidth'],
                            "outline_width_unit": "POINTS",
                        })

                    if data['opacity'] != '':
                        try:
                            opacity = float(data['opacity'])
                            setting_symbol.setOpacity(opacity)
                        except ValueError:
                            pass
                elif layer_info['geometryType'] == 'line':
                    setting_symbol = QgsLineSymbol.createSimple({
                        'color': data['borderColor'],
                        'line_style': data['borderStyle'],
                        'width': data['lineWidth'],
                        "width_unit": "POINTS",
                    })
                elif layer_info['geometryType'] == 'point':
                    setting_symbol = QgsMarkerSymbol.createSimple({
                        'size': data['size'],
                        "size_unit": "POINTS",
                        'color': data['fillColor'],
                        'line_color': data['borderColor'],
                        'line_width': data['lineWidth'],
                        'line_width_unit': 'POINTS'
                    })
                else:
                    return

                rule.setLabel(data['value'])
                rule.setFilterExpression(data['rule'])
                rule.setSymbol(setting_symbol)

                root_rule.appendChild(rule)
            except Exception:
                print(f"Unable to set rules.{data['value']}:{data['rule']}")

        root_rule.removeChildAt(0)
        self.layer.setRenderer(renderer)
        self.layer.triggerRepaint()

    def parse_color(self, color_str):
        """
        r,g,b,a'形式の文字列をQColorオブジェクトに変換する関数

        :param color_str: 'r,g,b,a'形式の色文字列
        :type color_str: str

        :return: QColorオブジェクト
        :rtype: QColor
        """
        r, g, b, a = map(int, color_str.split(","))
        return QColor(r, g, b, a)

    def hashed_layer(self, data, hashed):
        """
        斜線模様のフィルパターンを持つシンボルを設定する関数

        :param data: レイヤー設定情報
        :type data: dict
        :param hashed: 斜線模様の種類
        :type hashed: str

        :return: 設定されたシンボル
        :rtype: QgsFillSymbol
        """
        symbol = QgsFillSymbol()

        fill_layer = QgsSimpleFillSymbolLayer()
        fill_layer.color().setAlpha(0)

        symbol.symbolLayers()[0] = fill_layer
        symbol.setOutputUnit(QgsUnitTypes.RenderPoints)

        if symbol.symbolLayer(0) and isinstance(
            symbol.symbolLayer(0), QgsSimpleFillSymbolLayer
        ):
            outline_layer = symbol.symbolLayer(0)
            outline_layer.setStrokeColor(self.parse_color(data['borderColor']))
            outline_layer.setStrokeWidth(float(data['lineWidth']))
            outline_layer.setStrokeStyle(Qt.SolidLine)
            outline_layer.setColor(QColor(255, 255, 255, 0))
            outline_layer.setStrokeWidthUnit(QgsUnitTypes.RenderPoints)

        line_pattern = QgsLinePatternFillSymbolLayer()
        line_symbol = QgsLineSymbol()
        line_symbol.setColor(self.parse_color(data['fillColor']))
        line_symbol.setWidth(0.3)
        line_symbol.setWidthUnit(QgsUnitTypes.RenderPoints)
        line_pattern.setSubSymbol(line_symbol)
        line_pattern.setDistance(float(data['fillPattern_interval']))
        line_pattern.setDistanceUnit(QgsUnitTypes.RenderPoints)
        line_pattern.setOffsetUnit(QgsUnitTypes.RenderPoints)
        line_pattern.setStrokeWidthUnit(QgsUnitTypes.RenderPoints)
        if hashed == 'Bhashed':
            line_pattern.setLineAngle(45)
        else:
            line_pattern.setLineAngle(135)

        symbol.insertSymbolLayer(0, line_pattern)
        return symbol

    def coloring(self, item_val, year):
        """
        アイテムの値と年に基づいて地図レイヤーに色を付ける操作を実行する関数

        :param item_val: アイテムの値
        :type item_val: str
        :param year: 年
        :type year: str
        """
        rootchildren = QgsProject.instance().layerTreeRoot().children()

        for layer in rootchildren:
            layer.setItemVisibilityChecked(False)

        layer_order = []

        for layer_info in self.layer_config[item_val][year]:
            if 'yyyy' in layer_info['column']:
                layer_info['column'] = (
                    layer_info['column'].replace('yyyy', year)
                )

            # 誘導区域レイヤが存在せず、かつtype_id=31（居住誘導区域）の場合、仮想居住誘導区域を使用
            layer_name = layer_info['name']
            layers = QgsProject.instance().mapLayersByName(layer_name)

            if not layers and layer_name == '誘導区域':
                is_residential_induction = False
                for data in layer_info.get('data', []):
                    if data.get('label_name') == '居住誘導区域':
                        is_residential_induction = True
                        break

                if is_residential_induction:
                    layers = QgsProject.instance().mapLayersByName('仮想居住誘導区域')
                    if layers:
                        layer_info['name'] = '仮想居住誘導区域'
                        for data in layer_info.get('data', []):
                            if data.get('label_name') == '居住誘導区域':
                                data['label_name'] = '仮想居住誘導区域'

            if not layers:
                print(f"レイヤーが見つかりません: {layer_name}")
                continue

            targetlayer = layers[0]
            self.iface.setActiveLayer(targetlayer)
            self.layer = self.iface.activeLayer()
            QgsProject.instance().layerTreeRoot().findLayer(
                targetlayer.id()
            ).setItemVisibilityChecked(True)

            if self.layer is None:
                print("Layer not found")
                continue

            # QML処理部分の修正（coloringメソッド内）
            if self.layer.name() == layer_info['name']:
                qml_applied = False

                # QMLファイル検索・適用を試行
                new_structure_qml_path = self.construct_qml_path_from_structure(layer_info, item_val)
                if new_structure_qml_path:
                    qml_applied = self.apply_qml_style(new_structure_qml_path, year)

                # QMLファイルが見つからない、または適用に失敗した場合はXMLベースのスタイルにフォールバック
                if not qml_applied:
                    print(f"QMLファイルが見つからないため、XMLベースのスタイルを適用: {layer_info['name']}")
                    self._apply_xml_based_style(layer_info)

            if layer_info['scale-visibility'] == 'true':
                self.layer.setScaleBasedVisibility(True)
                try:
                    min_scale = float(layer_info['scale-min'])
                    self.layer.setMinimumScale(min_scale)
                except ValueError:
                    self.layer.setMinimumScale(250000.0)
                try:
                    max_scale = float(layer_info['scale-max'])
                    self.layer.setMaximumScale(max_scale)
                except ValueError:
                    self.layer.setMaximumScale(100.0)

            target_node = QgsProject.instance().layerTreeRoot().findLayer(
                targetlayer.id()
            )
            layer_no = layer_info.get('layerNo')

            if layer_no is not None:
                try:
                    layer_no_int = int(layer_no)
                    layer_order.append(
                        (layer_no_int, target_node, layer_info['name'])
                    )
                except ValueError:
                    pass

        layer_order.sort(key=lambda x: x[0])
        layer_tree = QgsProject.instance().layerTreeRoot()
        for _, (layer_no_int, target_node, layer_name) in enumerate(
            layer_order
        ):
            node_clone = target_node.clone()
            if layer_name == 'OpenStreetMap':
                layer_tree.insertChildNode(-1, node_clone)
            else:
                layer_tree.insertChildNode(0, node_clone)
            layer_tree.removeChildNode(target_node)

    def apply_qml_style(self, qml_path, year):
        """
        QMLファイルからスタイルを適用する関数

        :param qml_path: QMLファイルのパス
        :type qml_path: str

        :return: スタイル適用の成功/失敗
        :rtype: bool
        """
        if not qml_path:
            return False

        original_qml_path = qml_path

        try:
            # QMLファイルを動的に書き換え
            tmp_qml_path = self._replace_year_in_qml(qml_path, year)
            qml_path = tmp_qml_path

            # QMLファイルからスタイルを読み込み
            result = self.layer.loadNamedStyle(qml_path)

            if result[1]:  # 成功した場合
                self.layer.triggerRepaint()
                print(f"QMLスタイルを適用しました: {original_qml_path}")
                return True
            else:
                print(f"QMLスタイルの適用に失敗しました: {result[0]}")
                return False

        except Exception as e:
            print(f"QMLスタイル適用中にエラーが発生しました: {str(e)}")
            return False

        finally:
            # 一時ファイルを削除
            if tmp_qml_path and os.path.exists(tmp_qml_path):
                try:
                    os.remove(tmp_qml_path)
                except Exception as e:
                    print(f"一時ファイル削除エラー: {e}")

    def _replace_year_in_qml(self, qml_path, year):
        """
        一時ファイルを使用して、QMLファイル内のattr属性の年次部分を置換

        :param qml_path: 元のQMLファイルパス
        :type qml_path: str
        :param year: 置換する年次
        :type year: str

        :return: 一時QMLファイルのパス
        :rtype: str
        """
        # QMLファイルを読み込み
        with open(qml_path, 'r', encoding='utf-8') as qml_file:
            qml_content = qml_file.read()

        # 年次を置換
        qml_content = re.sub(
            r'(attr="[^"]*?)_(\d{4})(")', # "_年次"のパターン
            f'\\g<1>_{year}\\g<3>',
            qml_content
        )
        qml_content = re.sub(
            r'(attr=")(\d{4})_([^"]*?")', # "年次_"のパターン
            f'\\g<1>{year}_\\g<3>',
            qml_content
        )

        # 一時ファイルに保存
        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.qml',
            delete=False,
            encoding='utf-8'
        ) as tmp_file:
            tmp_file.write(qml_content)
            tmp_qml_path = tmp_file.name

        return tmp_qml_path

    def _apply_xml_based_style(self, layer_info):
        """
        XMLベースのスタイル適用（従来の処理）
        """
        if layer_info['type'] == 'categorized':
            self.apply_categorized_style(layer_info)
        elif layer_info['type'] == 'graduated':
            self.apply_graduated_style(layer_info)
        elif layer_info['type'] == 'ruled':
            self.apply_ruled_style(layer_info)
        elif layer_info['type'] == 'single':
            self.apply_single_style(layer_info)
