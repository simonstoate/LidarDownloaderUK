# SPDX-FileCopyrightText: 2025-2026 Simon Stoate
# SPDX-License-Identifier: GPL-2.0-or-later
"""The styles the plugin gives what it loads: elevation colours for DTMs / DSMs, and the survey dates layer.

Built with the QGIS API, so they work on every QGIS version.
"""

import warnings

from qgis.PyQt.QtGui import QColor
from qgis.core import (
    Qgis, QgsCategorizedSymbolRenderer, QgsColorRampShader, QgsFillSymbol, QgsPalLayerSettings, QgsRasterBandStats,
    QgsRasterShader, QgsRendererCategory, QgsSingleBandPseudoColorRenderer, QgsStyle, QgsTextBufferSettings,
    QgsTextFormat, QgsVectorLayerSimpleLabeling)


def elevation_colours(layer):
    """Colour an elevation raster by height: a colour ramp stretched over its values (low blue, high red)."""
    provider = layer.dataProvider()
    # A sample is enough for a colour stretch and fast even on large mosaics
    with warnings.catch_warnings():
        # QGIS 3.40+ bindings report bandStatistics() as deprecated whatever is passed, even the new
        # Qgis.RasterBandStatistic values; the result is correct
        warnings.simplefilter('ignore', DeprecationWarning)
        stats = provider.bandStatistics(1, _min_max_flags(), layer.extent(), 250000)
    low, high = stats.minimumValue, stats.maximumValue
    if not high > low:
        high = low + 1
    ramp = QgsStyle.defaultStyle().colorRamp('Spectral')
    ramp.invert()  # low = blue/green, high = red
    shader_function = QgsColorRampShader(low, high, ramp)
    shader_function.classifyColorRamp(10, 1, layer.extent(), provider)
    shader = QgsRasterShader(low, high)
    shader.setRasterShaderFunction(shader_function)
    layer.setRenderer(QgsSingleBandPseudoColorRenderer(provider, 1, shader))


def _min_max_flags():
    # The statistics flags moved to Qgis.RasterBandStatistic in newer QGIS versions
    if hasattr(Qgis, 'RasterBandStatistic'):
        return Qgis.RasterBandStatistic.Min | Qgis.RasterBandStatistic.Max
    return QgsRasterBandStats.Stats.Min | QgsRasterBandStats.Stats.Max


def style_survey_dates(layer):
    """Survey areas coloured by the year they were flown (oldest red, newest blue), labelled with
    the year; the map tip shows the survey and its flown dates."""
    years = sorted(y for y in layer.uniqueValues(layer.fields().indexOf('year')) if isinstance(y, int))
    ramp = QgsStyle.defaultStyle().colorRamp('Spectral')
    categories = []
    for i, year in enumerate(years):
        colour = ramp.color(i / (len(years) - 1) if len(years) > 1 else 1.0) if ramp else QColor('#3182bd')
        symbol = QgsFillSymbol.createSimple({
            'color': f'{colour.red()},{colour.green()},{colour.blue()},110',
            'outline_color': colour.darker(150).name(), 'outline_width': '0.3'})
        categories.append(QgsRendererCategory(year, symbol, str(year)))
    layer.setRenderer(QgsCategorizedSymbolRenderer('year', categories))

    settings = QgsPalLayerSettings()
    settings.fieldName = 'year'
    settings.setFormat(_halo_text(8, '#222222'))
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    layer.setDisplayExpression('"survey"')
    layer.setMapTipTemplate('<b>Surveyed [% "year" %]</b><br>Flown [% format_date("flown_from", \'d MMM yyyy\') %]'
                            '[% CASE WHEN "flown_to" <> "flown_from" THEN \' to \' || '
                            'format_date("flown_to", \'d MMM yyyy\') END %]<br>'
                            '[% CASE WHEN "type" IS NOT NULL THEN "type" || \' survey \' ELSE \'Survey \' END %]'
                            '[% "survey" %], [% "resolution" %] m, tile [% "tile" %]')
    layer.triggerRepaint()


def _halo_text(size, colour):
    text = QgsTextFormat()
    text.setSize(size)
    text.setColor(QColor(colour))
    buffer = QgsTextBufferSettings()
    buffer.setEnabled(True)
    buffer.setSize(0.7)
    buffer.setColor(QColor('white'))
    text.setBuffer(buffer)
    return text
