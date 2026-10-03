# SPDX-FileCopyrightText: 2025-2026 Simon Stoate
# SPDX-License-Identifier: GPL-2.0-or-later
"""The styles the plugin gives what it loads: elevation colours for DTMs / DSMs, and the survey dates layer.

Built with the QGIS API, so they work on every QGIS version.
"""

import warnings

from qgis.PyQt.QtGui import QColor
from qgis.core import (
    Qgis, QgsCategorizedSymbolRenderer, QgsColorRampShader, QgsFeatureRequest, QgsFillSymbol, QgsPalLayerSettings,
    QgsProperty, QgsRasterBandStats, QgsRasterShader, QgsRendererCategory, QgsSingleBandPseudoColorRenderer, QgsStyle,
    QgsTextBufferSettings, QgsTextFormat, QgsVectorLayerSimpleLabeling)


def height_range(layer):
    """(lowest, highest) value of an elevation raster's first band."""
    # A sample is enough for a colour stretch and fast even on large mosaics
    with warnings.catch_warnings():
        # QGIS 3.40+ bindings report bandStatistics() as deprecated whatever is passed, even the new
        # Qgis.RasterBandStatistic values; the result is correct
        warnings.simplefilter('ignore', DeprecationWarning)
        stats = layer.dataProvider().bandStatistics(1, _min_max_flags(), layer.extent(), 250000)
    return stats.minimumValue, stats.maximumValue


def elevation_colours(layer, value_range=None):
    """Colour an elevation raster by height: a colour ramp (low blue, high red) stretched over value_range (low,
    high), by default the layer's own heights. Only how it's drawn changes, never the file. The tiles of a dataset
    are given one range, so neighbours match at their edges."""
    provider = layer.dataProvider()
    low, high = value_range or height_range(layer)
    if not high > low:
        high = low + 1
    ramp = QgsStyle.defaultStyle().colorRamp('Spectral')
    ramp.invert()  # low = blue/green, high = red
    shader_function = QgsColorRampShader(low, high, ramp)
    shader_function.classifyColorRamp(10, 1, layer.extent(), provider)
    shader = QgsRasterShader(low, high)
    shader.setRasterShaderFunction(shader_function)
    renderer = QgsSingleBandPseudoColorRenderer(provider, 1, shader)
    renderer.setClassificationMin(low)  # what the layer's Symbology tab shows as Min / Max
    renderer.setClassificationMax(high)
    layer.setRenderer(renderer)


def colour_range(layer):
    """The (low, high) an elevation layer is coloured over, or None if it isn't coloured by height."""
    renderer = layer.renderer()
    if isinstance(renderer, QgsSingleBandPseudoColorRenderer):
        return renderer.classificationMin(), renderer.classificationMax()
    return None


def colour_signature(layer):
    """Text that changes whenever a layer's colours do (its renderer, range and colour classes): the plugin keeps it
    to tell whether the user has restyled a layer since the plugin coloured it."""
    renderer = layer.renderer()
    if not isinstance(renderer, QgsSingleBandPseudoColorRenderer):
        return type(renderer).__name__ if renderer is not None else 'none'
    shader = renderer.shader()
    function = shader.rasterShaderFunction() if shader is not None else None
    items = function.colorRampItemList() if hasattr(function, 'colorRampItemList') else []
    return (f'{renderer.classificationMin()!r}|{renderer.classificationMax()!r}|'
            + ';'.join(f'{item.value:.6g}:{item.color.name()}' for item in items))


def _min_max_flags():
    # The statistics flags moved to Qgis.RasterBandStatistic in newer QGIS versions
    if hasattr(Qgis, 'RasterBandStatistic'):
        return Qgis.RasterBandStatistic.Min | Qgis.RasterBandStatistic.Max
    return QgsRasterBandStats.Stats.Min | QgsRasterBandStats.Stats.Max


def survey_years(layer):
    """The years flown in a survey dates layer, oldest first."""
    return sorted(y for y in layer.uniqueValues(layer.fields().indexOf('SRVY_YEAR')) if isinstance(y, int))


def survey_signature(layer):
    """Text that changes whenever a survey dates layer's colours do: kept to tell whether the user has restyled it."""
    renderer = layer.renderer()
    if not isinstance(renderer, QgsCategorizedSymbolRenderer):
        return type(renderer).__name__ if renderer is not None else 'none'
    return renderer.classAttribute() + '|' + ';'.join(f'{c.value()}:{c.symbol().color().name()}'
                                                      for c in renderer.categories())


def style_survey_dates(layer, span=None):
    """A tile's survey file as it came (its surveys, with SRVY_YEAR, SD_FLOWN / ED_FLOWN, FILENAME...): coloured by the
    year flown over span (first, last year: those of every tile's survey dates shown, so a year has one colour in all
    of them; by default its own), the oldest red and the newest blue, and labelled with the year; the map tip shows
    the survey and its flown dates (and, for the SurfZone DEM, the kind of survey). Where surveys overlap the newest
    is drawn on top, as it's the one used in the merged tile; the layer as a whole is see-through, so the ground shows
    under it without the surveys' colours mixing."""
    names = layer.fields().names()
    years = survey_years(layer)
    ramp = QgsStyle.defaultStyle().colorRamp('Spectral')
    first, last = span or ((years[0], years[-1]) if years else (0, 0))
    categories = []
    for year in years:
        fraction = min(max((year - first) / (last - first), 0.0), 1.0) if last > first else 1.0
        colour = ramp.color(fraction) if ramp else QColor('#3182bd')
        symbol = QgsFillSymbol.createSimple({
            'color': f'{colour.red()},{colour.green()},{colour.blue()},255',
            'outline_color': colour.darker(150).name(), 'outline_width': '0.3'})
        categories.append(QgsRendererCategory(year, symbol, str(year)))
    renderer = QgsCategorizedSymbolRenderer('SRVY_YEAR', categories)
    renderer.setOrderBy(QgsFeatureRequest.OrderBy([QgsFeatureRequest.OrderByClause('"SRVY_YEAR"', True)]))
    renderer.setOrderByEnabled(True)
    layer.setRenderer(renderer)
    layer.setOpacity(0.5)

    settings = QgsPalLayerSettings()
    settings.fieldName = 'SRVY_YEAR'
    settings.setFormat(_halo_text(8, '#222222'))
    # Surveys overlapping in the middle of a tile would put their labels in the same place: the newest's wins, as
    # it's the one drawn on top
    settings.dataDefinedProperties().setProperty(
        QgsPalLayerSettings.Property.Priority,
        QgsProperty.fromExpression('scale_linear("SRVY_YEAR", 1998, 2030, 0, 10)'))
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    layer.setDisplayExpression('"FILENAME"')
    tip = '<b>Surveyed [% "SRVY_YEAR" %]</b>'
    if 'SD_FLOWN' in names and 'ED_FLOWN' in names:
        tip += ('<br>Flown [% format_date("SD_FLOWN", \'d MMM yyyy\') %]'
                '[% CASE WHEN "ED_FLOWN" <> "SD_FLOWN" THEN \' to \' || format_date("ED_FLOWN", \'d MMM yyyy\') END %]')
    tip += '<br>'
    tip += ('[% CASE WHEN "SRVY_TYPE" IS NOT NULL THEN "SRVY_TYPE" || \' survey \' ELSE \'Survey \' END %]'
            if 'SRVY_TYPE' in names else 'Survey ')
    tip += '[% "FILENAME" %]' + (', [% "RESOLUTION" %] m' if 'RESOLUTION' in names else '')
    layer.setMapTipTemplate(tip)
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
