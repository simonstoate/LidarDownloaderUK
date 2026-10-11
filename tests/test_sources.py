"""Unit tests for sources.py (Wales and Scotland), against real catalogue responses captured in tests/data."""

import json
import os

from lidar_downloader_uk import api, sources

DATA = os.path.join(os.path.dirname(__file__), 'data')


def load(name):
    with open(os.path.join(DATA, name), encoding='utf-8') as f:
        return json.load(f)


def test_square_tiles():
    assert api.square_tiles('SH2281') == ('SH28SW', ['SH28SW'])
    assert api.square_tiles('NS7593') == ('NS79SE', ['NS79SE'])   # 5 km east, 3 km north in NS79
    assert api.square_tiles('NS7098') == ('NS79NW', ['NS79NW'])
    assert api.square_tiles('ns70se') == ('NS70SE', ['NS70SE'])
    assert api.square_tiles('ST16 ') == ('ST16', ['ST16NE', 'ST16NW', 'ST16SE', 'ST16SW'])
    # A 500 m square: a quarter of a 1 km square (the Welsh archive writes some in lower case)
    assert api.square_tiles('ST3281SW') == ('ST38SW', ['ST38SW'])
    assert api.square_tiles('SN2551sw ') == ('SN25SE', ['SN25SE'])
    for bad in ('', None, 'SI12', 'SH228', 'SH12XX', '../SH12', 'SH2281XX', 'SH2281S'):
        assert api.square_tiles(bad) is None


def test_sources_for_square():
    assert sources.sources_for_square('SU') == {'ea'}
    assert sources.sources_for_square('ST') == {'ea', 'wales'}
    assert sources.sources_for_square('SH') == {'ea', 'wales'}
    assert sources.sources_for_square('NT') == {'ea', 'scotland'}
    assert sources.sources_for_square('NN') == {'scotland'}
    assert sources.sources_for_square('HY') == {'scotland'}


def test_trusted_download_links():
    for url in ('https://dmwproductionblob.blob.core.windows.net/lidar-zips/2020-22/dtm/x.tif',
                'https://lle.blob.core.windows.net/lidar/2m_res_ST16_1998_dsm.zip',
                'https://srsp-open-data.s3.eu-west-2.amazonaws.com/lidar/phase-1/dtm/27700/gridded/NS79_1M_DTM.tif',
                'https://srsp-open-data.s3-eu-west-2.amazonaws.com/lidar/phase-1/laz/x.laz'):
        assert api.is_trusted_url(url), url
    for url in ('http://lle.blob.core.windows.net/lidar/x.zip', 'https://example.com/lidar/x.tif',
                'https://srsp-open-data.s3.eu-west-2.amazonaws.com/../x.tif'):
        assert not api.is_trusted_url(url), url


def test_wales_national():
    offerings = sources.parse_wales_national(load('wales_national_SH28.json'))
    by_product = {o.dataset.product: o for o in offerings}
    assert set(by_product) == {'wales_lidar_dtm', 'wales_lidar_dsm'}
    dtm = by_product['wales_lidar_dtm']
    # One survey for the whole 2020-22 programme, so a tile flown over two years stays one dataset
    assert dtm.dataset == api.Dataset('wales_lidar_dtm', '2020-2022', '1') and dtm.tile == 'SH28SW'
    assert len(dtm.files) == 10 and all(f.square == 'SH28SW' and f.url.startswith('https://') for f in dtm.files)
    assert api.dataset_label(dtm.dataset) == 'Wales DTM (national survey), 2020-22, 1 m'
    assert api.dataset_folder(dtm.dataset) == os.path.join('wales_lidar_dtm', '1m', '2020-2022')


def test_wales_archive():
    offerings = sources.parse_wales_archive(load('wales_archive_ST16.json'))
    assert offerings and all(o.dataset.product in ('wales_lidar_archive_dtm', 'wales_lidar_archive_dsm')
                             for o in offerings)
    # Each zip covers a 10 km square: kept once in its own folder, shared by its tiles
    assert all(f.square == 'ST16' and f.name.endswith('.zip') for o in offerings for f in o.files)
    years = {o.dataset.year for o in offerings}
    assert '1998' in years and '2005-2006' in years
    assert {o.dataset.resolution for o in offerings} <= {'2', '1', '0.5', '0.25'}


def archive_row(name, x0=None, y0=None, size=2000, square='ST17', year='2008', resolution=1, flown='31-July-2008'):
    """An archive catalogue row as DataMapWales lists it (5 Oct 2026): named by the 1 km square at its south-west
    corner, its outline a fraction of a millimetre off the whole metre; no outline when x0 is None."""
    zip_name = f'{resolution * 100:g}cm_res' if resolution < 1 else f'{resolution:g}m_res'
    row = {'type': 'Feature', 'properties': {
        'gb_ng': f'{name}  ', '_10k': f'{square} ', 'year': year, 'resolution': resolution, 'date_flown': flown,
        'lidar_name': 'D0085406    ',
        'dtm_url': f'lle.blob.core.windows.net/lidar/{zip_name}_{square}_{year}_dtm.zip', 'dsm_url': ''}}
    if x0 is not None:
        a, b = x0 + 0.0001, y0 + 0.0001
        c, d = x0 + size - 0.0001, y0 + size - 0.0001
        row['geometry'] = {'type': 'Polygon', 'coordinates': [[[a, b], [a, d], [c, d], [c, b], [a, b]]]}
    return row


def test_wales_archive_outlines():
    def tiles(*rows):
        return sorted(o.tile for o in sources.parse_wales_archive({'features': list(rows)}))

    # A 2 km tile across the 5 km line at E 315000 is offered for both tiles it covers; one that only reaches the
    # line, for its own
    assert tiles(archive_row('ST1476', 314000, 176000)) == ['ST17NE', 'ST17NW']
    assert tiles(archive_row('ST1376', 313000, 176000)) == ['ST17NW']
    assert tiles(archive_row('ST1874', 318000, 174000)) == ['ST17NE', 'ST17SE']
    # 500 m tiles (the 25 cm surveys of 2006-2011), by their outline or, without one, by their name
    quarter = archive_row('SN2551sw', 325000, 251000, size=500, square='SN25', year='2006', resolution=0.25)
    assert tiles(quarter) == ['SN25SE']
    assert tiles(archive_row('ST3281SW', square='ST38', resolution=0.25)) == ['ST38SW']
    offering, = sources.parse_wales_archive({'features': [quarter]})
    assert offering.dataset == api.Dataset('wales_lidar_archive_dtm', '2006', '0.25')
    assert offering.files[0].name == '25cm_res_SN25_2006_dtm.zip' and offering.files[0].square == 'SN25'
    # A misspelt name (SN919 for SN9109) goes by its outline
    assert tiles(archive_row('SN919', 291000, 209000, size=1000, square='SN90', resolution=0.5)) == ['SN90NW']
    # Only tiles in the zip's own 10 km square (its files are kept in that square's folder)
    assert tiles(archive_row('ST1476', 314000, 176000, square='ST16')) == []
    assert tiles(archive_row('ST1476', square='ST16')) == []
    # A row with an unusable outline (here, the size of the country) falls back to its name
    assert tiles(archive_row('ST1476', 0, 0, size=700000)) == ['ST17NW']


def test_wales_survey_dates():
    # The national survey: each 1 km square, with the date it was flown (the catalogue writes 22-03-2020)
    dtm = next(o for o in sources.parse_wales_national(load('wales_national_SH28.json'))
               if o.tile == 'SH28SW' and o.dataset.product == 'wales_lidar_dtm')
    assert len(dtm.surveys) == 10 and {(a.kind, a.name) for a in dtm.surveys} == {('Welsh Government national',
                                                                                  '2020-22')}
    assert api.SurveyArea((222000, 381000, 223000, 382000), 2020, '22 Mar 2020', 'Welsh Government national',
                          '2020-22') in dtm.surveys
    assert sorted({(a.year, a.flown) for a in dtm.surveys}) == [(2020, '22 Mar 2020'), (2022, '19 Mar 2022')]
    # The archive: each catalogue tile's outline (rounded to the metre) or the square it's named by, its date as
    # text to read (it's free text in the catalogue), and the survey named as its zip is
    offering, = sources.parse_wales_archive({'features': [archive_row('ST1476', 314000, 176000)]})[:1]
    assert offering.surveys == (api.SurveyArea((314000, 176000, 316000, 178000), 2008, '31 July 2008', 'NRW archive',
                                               '1m_res_ST17_2008'),)
    quarter, = sources.parse_wales_archive({'features': [archive_row('ST3281sw', square='ST38', resolution=0.25,
                                                                     year='2005-2006', flown='Nov-05---Jan-06')]})
    assert quarter.surveys == (api.SurveyArea((332000, 181000, 332500, 181500), 2005, 'Nov 05 to Jan 06',
                                              'NRW archive', '25cm_res_ST38_2005-2006'),)
    for text, readable in (('1---5-Apr-2009', '1 to 5 Apr 2009'), ('Oct---Nov-2008', 'Oct to Nov 2008'),
                           ('28-March-1998', '28 March 1998'), ('1--29-Sep-2011', '1 to 29 Sep 2011'),
                           ('October-2002', 'October 2002'), ('', '')):
        assert sources.flown_text(text) == readable
    assert sources.name_bounds('st1476 ') == (314000, 176000, 315000, 177000)
    assert sources.name_bounds('SN2551NE') == (225500, 251500, 226000, 252000)
    assert sources.name_bounds('SN919') is None and sources.name_bounds('ST17') is None
    # The same tile listed twice (as a 10 km file can be): one offering, its areas kept once
    twice = sources.parse_wales_archive({'features': [archive_row('ST1476', 314000, 176000)] * 2})
    assert len(twice) == 2 and all(len(o.surveys) == 1 for o in twice)  # (ST17NE and ST17NW)


def test_scotland_collections():
    collections = sources.parse_scotland_collections(load('scotland_collections.json'))
    assert 'scotland-gov/lidar/ogc' not in collections and len(collections) == 50
    phase1 = collections['scotland-gov/lidar/phase-1/dtm']
    assert (phase1.product, phase1.year) == ('scotland_lidar_dtm', '2011-phase-1') and not phase1.non_commercial
    assert collections['scotland-gov/lidar/phase-2/laz'].non_commercial
    assert collections['scotland-gov/lidar/hes/hes-luing/dtm'].non_commercial
    hes_laz = collections['scotland-gov/lidar/hes/hes-2010/laz/dsm']
    assert hes_laz.product == 'scotland_lidar_point_cloud' and hes_laz.year == '2010-hes-dsm'
    assert api.year_label(api.Dataset('scotland_lidar_dtm', '2019-outerheb', '0.5')) == '2019 (Outer Hebrides)'
    assert api.year_label(api.Dataset('scotland_lidar_dtm', '2011-phase-1', '1')) == '2011 (Phase 1)'
    assert api.dataset_from_folder('scotland_lidar_dtm', ['1m', '2011-phase-1']) == \
        api.Dataset('scotland_lidar_dtm', '2011-phase-1', '1')
    assert api.dataset_from_folder('scotland_lidar_point_cloud', ['2017-phase-4']) == \
        api.Dataset('scotland_lidar_point_cloud', '2017-phase-4', 'NaN')


def test_scotland_products():
    collections = sources.parse_scotland_collections(load('scotland_collections.json'))
    offerings = sources.parse_scotland_products(load('scotland_products_stirling.json'), collections)
    phase1 = [o for o in offerings if o.dataset == api.Dataset('scotland_lidar_dtm', '2011-phase-1', '1')]
    # Phase I DTMs are 10 km files, kept in the NS79 folder. The catalogue gives one the whole square, but it's
    # placed by its survey's point clouds (1 km files): here (a search of NS7893-NS7994) they're in NS79SE
    assert [o.tile for o in phase1] == ['NS79SE']
    assert {(f.name, f.square) for o in phase1 for f in o.files} == {('NS79_1M_DTM_PHASE1.tif', 'NS79')}
    assert phase1[0].files[0].size > 1_000_000 and 'attribution' in phase1[0].rights.lower()
    assert sorted(a.bounds for a in phase1[0].surveys) == [
        (278000, 693000, 279000, 694000), (279000, 693000, 280000, 694000)]
    assert {a.name for a in phase1[0].surveys} == {'phase-1'}
    phase5 = [o for o in offerings if o.dataset == api.Dataset('scotland_lidar_dtm', '2021-phase-5', '0.5')]
    assert [o.tile for o in phase5] == ['NS79SE'] and phase5[0].files[0].square == 'NS79SE'
    clouds = [o for o in offerings if o.dataset.product == 'scotland_lidar_point_cloud']
    assert clouds and all(o.dataset.resolution == 'NaN' for o in clouds)
    # "Newest for each tile": Phase 5's 5 km file has point clouds for one of these squares only (its DTM of NS79SE
    # is almost empty, checked October 2026), so Phase 4, which covers them all, is chosen
    tile = [o for o in offerings if o.tile == 'NS79SE']
    assert [len(api.coverage_cells(o, tile)) / 4 for o in phase5] == [1.0]
    best = api.pick_offering(tile, api.Dataset('scotland_lidar_dtm', 'latest', ''))
    assert best.dataset.year == '2017-phase-4'
    assert api.pick_offering(tile, phase5[0].dataset) is phase5[0]  # (chosen by name, it's still offered)
    assert api.partial_cover(phase5[0].dataset, {'NS79SE': tile}, ['NS79SE']) == (1.0, 4.0)
    assert api.partial_cover(best.dataset, {'NS79SE': tile}, ['NS79SE']) is None


def scottish_product(collection, ref, name):
    return {'collectionName': f'scotland-gov/lidar/{collection}', 'properties': {'osgbGridRef': ref},
            'data': {'product': {'http': {'url': f'https://srsp-open-data.s3.eu-west-2.amazonaws.com/lidar/{name}',
                                          'size': 1000}}}}


def test_scottish_files_placed_by_their_surveys_smaller_files():
    surveys = (('phase-1/dtm', '2011'), ('phase-1/laz', '2011'), ('phase-2/dtm', '2012'), ('phase-2/laz', '2012'),
               ('phase-6/dtm', '2021'))
    collections = {f'scotland-gov/lidar/{c}': sources.scottish_collection(f'scotland-gov/lidar/{c}', {
        'temporalExtent': {'begin': year}}) for c, year in surveys}
    assert collections['scotland-gov/lidar/phase-1/laz'].survey == 'phase-1'
    data = {'result': [
        # A Phase 1 10 km file with point clouds in two squares of HY63SW: offered there only
        scottish_product('phase-1/dtm', 'HY63', 'HY63_1M_DTM_PHASE1.tif'),
        scottish_product('phase-1/laz', 'HY6430', 'HY6430_2PPM_LAS_PHASE1.laz'),
        scottish_product('phase-1/laz', 'HY6431', 'HY6431_2PPM_LAS_PHASE1.laz'),
        # A Phase 2 10 km file with a point cloud of one 5 km tile (as Phase 2 has): that tile
        scottish_product('phase-2/dtm', 'NT19', 'NT19_1M_DTM_PHASE2.tif'),
        scottish_product('phase-2/laz', 'NT19SE', 'NT19SE_2PPM_LAS_PHASE2.laz'),
        # A 10 km file of a survey with no smaller files here: the whole square, as before
        scottish_product('phase-6/dtm', 'NS56', 'NS56_50CM_DTM_PHASE6.tif'),
    ]}
    offerings = sources.parse_scotland_products(data, collections)
    dtm = {}
    for o in offerings:
        if o.dataset.product == 'scotland_lidar_dtm':
            dtm.setdefault(o.files[0].name, []).append(o.tile)
    assert dtm == {'HY63_1M_DTM_PHASE1.tif': ['HY63SW'], 'NT19_1M_DTM_PHASE2.tif': ['NT19SE'],
                   'NS56_50CM_DTM_PHASE6.tif': ['NS56NE', 'NS56NW', 'NS56SE', 'NS56SW']}
    hy63 = next(o for o in offerings if o.files[0].name == 'HY63_1M_DTM_PHASE1.tif')
    assert len(api.coverage_cells(hy63)) == 8  # (two 1 km squares: 8 cells of 500 m)
    ns56 = next(o for o in offerings if o.files[0].name == 'NS56_50CM_DTM_PHASE6.tif')
    assert len(api.coverage_cells(ns56)) == 100


def test_newest_for_each_tile_passes_over_partial_surveys():
    def offering(year, squares):
        areas = tuple(api.SurveyArea(s, int(year[:4]), '', 'scotland', year) for s in squares)
        return api.Offering(api.Dataset('scotland_lidar_dtm', year, '0.5'), 'NS79SE', 'NS79SE', '', (), '', areas)
    tile = (275000, 690000, 280000, 695000)
    full = offering('2017-phase-4', [tile])
    new = offering('2025-national-lidar-programme', [(275000, 690000, 276000, 691000)])
    latest = api.Dataset('scotland_lidar_dtm', 'latest', '')
    assert api.pick_offering([full, new], latest) is full
    # A newer survey covering (nearly) all of it is chosen
    most = offering('2025-national-lidar-programme', [(275000, 690000, 280000, 694600)])  # 23 of 25 km2
    assert api.pick_offering([full, most], latest) is most
    assert api.partial_cover(new.dataset, {'NS79SE': [full, new]}, ['NS79SE']) == (1.0, 25.0)


def test_survey_names():
    def label(year):
        return api.year_label(api.Dataset('scotland_lidar_dtm', year, '0.5'))
    assert label('2026-coastal-coastal-2025') == '2025-26 (Coastal)'
    assert label('2023-orkney-islands-council-23') == '2023 (Orkney)'
    assert label('2016-hes-2016-2017') == '2016-17 (HES)'
    assert label('2025-national-lidar-programme') == '2025 (National LIDAR Programme)'
    assert label('2010-hes-2010s10-dsm') == '2010 (HES Scottish Ten DSM)'


def test_merge_offerings_dedupes_files():
    dataset = api.Dataset('wales_lidar_dtm', '2020-2022', '1')
    a = api.FileRef('https://lle.blob.core.windows.net/lidar/a.zip', 'a.zip', 'ST16')
    b = api.FileRef('https://lle.blob.core.windows.net/lidar/b.zip', 'b.zip', 'ST16')
    merged = sources.merge_offerings([api.Offering(dataset, 'ST16NE', 'ST16NE', '', (a,)),
                                      api.Offering(dataset, 'ST16NE', 'ST16NE', '', (a, b))])
    assert len(merged) == 1 and merged[0].files == (a, b)


def test_scotland_search_body():
    polygon = {'type': 'Polygon', 'coordinates': [[[-4, 56], [-3.9, 56], [-3.9, 56.1], [-4, 56.1], [-4, 56]]]}
    body = sources.scotland_search_body(['b', 'a'], polygon)
    assert body['collections'] == ['a', 'b'] and body['spatialop'] == 'intersects'
    assert body['footprint'] == 'POLYGON((-4 56, -3.9 56, -3.9 56.1, -4 56.1, -4 56))'


def test_wales_url():
    url = sources.wales_url(sources.WALES_NATIONAL_LAYER, (220000.4, 380000, 230000, 390000))
    assert 'bbox=220000,380000,230000,390000,EPSG:27700' in url and 'typeNames=geonode%3A' in url


def test_attribution_by_source():
    assert 'Welsh Government' in api.attribution(api.Dataset('wales_lidar_dtm', '2020-2022', '1'))
    assert 'Scottish Government' in api.attribution(api.Dataset('scotland_lidar_dtm', '2011-phase-1', '1'))
    assert api.attribution(api.Dataset('scotland_lidar_dtm', '2011-phase-1', '1'), 'Own statement') == 'Own statement'
    assert api.attribution(api.DEFAULT_DATASET).startswith('© Environment Agency copyright and/or database right 2022')


def test_server_names_that_would_make_odd_folders_are_ignored():
    assert sources.scottish_collection(r'scotland-gov/lidar/x\..\..\Startup/dtm', {'year': 2020}) is None
    assert not api.is_safe_dataset(api.Dataset('lidar_tiles_dtm', r'..\..\x', '1'))
    assert not api.is_safe_dataset(api.Dataset('lidar/../x', '2020', '1'))
    assert api.is_safe_dataset(api.Dataset('scotland_lidar_point_cloud', '2011-phase-1', 'NaN'))
    data = {'results': [{'product': {'id': 'lidar_tiles_dtm'}, 'year': {'id': r'..\..\x'},
                         'resolution': {'id': '1'}, 'tile': {'id': 'SU1525', 'label': 'SU12ne'}}]}
    assert api.parse_search_response(data) == []


def test_http_errors_by_service():
    assert api.describe_http_error(500).startswith('no data')           # the EA's answer for "nothing here"
    assert api.describe_http_error(500, 'other').startswith('server error')
    assert api.describe_http_error(404, 'other').startswith('no data')


def test_resolve_year():
    datasets = [api.Dataset('scotland_lidar_dtm', y, '1') for y in ('2011-phase-1', '2012-phase-2', '2010-hes')]
    datasets.append(api.Dataset('wales_lidar_dtm', '2020-2022', '1'))
    assert api.resolve_year('wales_lidar_dtm', '2020-22', datasets) == ('2020-2022', ['2020-2022'])
    assert api.resolve_year('wales_lidar_dtm', '2020', datasets)[0] == '2020-2022'
    assert api.resolve_year('scotland_lidar_dtm', '2011', datasets)[0] == '2011-phase-1'
    assert api.resolve_year('scotland_lidar_dtm', '2011 (Phase 1)', datasets)[0] == '2011-phase-1'
    assert api.resolve_year('scotland_lidar_dtm', '1999', datasets) == (None, [])


def test_same_year_surveys_rank_by_resolution():
    coarse = api.Dataset('scotland_lidar_dtm', '2017-phase-4', '1')
    fine = api.Dataset('scotland_lidar_dtm', '2017-hes-sp3', '0.5')
    assert api.best_dataset([coarse, fine]) == fine
    assert api.best_dataset([fine, api.Dataset('scotland_lidar_dtm', '2021-phase-5', '1')]).year == '2021-phase-5'


def test_services_named_when_not_responding():
    url = 'https://environment.data.gov.uk/tiles/collections/survey/lidar_composite_dtm/2022/1/SU1525'
    assert api.url_source(url) == 'ea'
    assert api.url_source('https://lle.blob.core.windows.net/lidar/2m_res_ST16_1998_dtm.zip') == 'wales'
    assert api.url_source('https://srsp-open-data.s3-eu-west-2.amazonaws.com/x.tif') == 'scotland'
    assert api.url_source('https://example.com/x') == ''
    message = api.not_responding('wales')
    assert message.startswith("DataMapWales isn't responding") and api.NOT_RESPONDING in message


def test_northern_ireland_links_are_elevation_data_on_the_current_site():
    # LIDAR and the DTMs made from it only (no flood maps or geology), and OpenDataNI's /search?q=...: its old
    # /dataset?... addresses answer "500 | Internal Server Error" since the site was rebuilt
    assert len(sources.NI_LINKS) == 2
    for label, url in sources.NI_LINKS:
        assert 'LIDAR' in label or 'DTM' in label
        assert url.startswith('https://www.opendatani.gov.uk/search?q=')
