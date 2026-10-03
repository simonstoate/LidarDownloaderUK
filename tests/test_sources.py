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
    for bad in ('', None, 'SI12', 'SH228', 'SH12XX', '../SH12'):
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
    # Phase I DTMs are 10 km files: one file, kept in the NS79 folder, offered for all four tiles
    assert sorted(o.tile for o in phase1) == ['NS79NE', 'NS79NW', 'NS79SE', 'NS79SW']
    assert {(f.name, f.square) for o in phase1 for f in o.files} == {('NS79_1M_DTM_PHASE1.tif', 'NS79')}
    assert phase1[0].files[0].size > 1_000_000 and 'attribution' in phase1[0].rights.lower()
    phase5 = [o for o in offerings if o.dataset == api.Dataset('scotland_lidar_dtm', '2021-phase-5', '0.5')]
    assert [o.tile for o in phase5] == ['NS79SE'] and phase5[0].files[0].square == 'NS79SE'
    clouds = [o for o in offerings if o.dataset.product == 'scotland_lidar_point_cloud']
    assert clouds and all(o.dataset.resolution == 'NaN' for o in clouds)
    # Newest survey first when choosing what to download for "latest available"
    best = api.pick_offering([o for o in offerings if o.tile == 'NS79SE'],
                             api.Dataset('scotland_lidar_dtm', 'latest', ''))
    assert best.dataset.year == '2021-phase-5'


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
