"""
/***************************************************************************
 *
 * Plateau統計可視化プロセシングプロバイダー
 *
 ***************************************************************************/
"""

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .algorithms.metric_calculation_processing import MetricCalculationProcessing


class PlateauProcessingProvider(QgsProcessingProvider):
    """Plateau統計可視化プロセシングプロバイダー"""
    
    def loadAlgorithms(self):
        """アルゴリズムを読み込み"""
        self.addAlgorithm(MetricCalculationProcessing())
    
    def id(self):
        """プロバイダーID"""
        return 'plateau_statistics'
    
    def name(self):
        """プロバイダー名"""
        return 'Plateau統計可視化'
    
    def longName(self):
        """プロバイダーの詳細名"""
        return 'Plateau Statistics Visualization Plugin'
    
    def icon(self):
        """プロバイダーアイコン"""
        return QIcon()  # デフォルトアイコンを使用