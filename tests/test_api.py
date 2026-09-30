# coding=utf-8
"""Unit tests for api.py (no QGIS needed). Run `python -m pytest` from the repo root."""

import json
import os
import unittest

from lidar_downloader_uk import api


class TileToGridTest(unittest.TestCase):

    def test_quadrants(self):
        self.assertEqual(api.tile_to_grid('SU12SW'), 'SU1020')
        self.assertEqual(api.tile_to_grid('SU12SE'), 'SU1520')
        self.assertEqual(api.tile_to_grid('SU12NW'), 'SU1025')
        self.assertEqual(api.tile_to_grid('SU12NE'), 'SU1525')

    def test_lowercase_and_zero_padding(self):
        self.assertEqual(api.tile_to_grid('tq00sw'), 'TQ0000')
        self.assertEqual(api.tile_to_grid('TQ09NE'), 'TQ0595')

    def test_invalid(self):
        for bad in ('', 'SU12', 'SU1XNE', 'SU12XX', '1212NE'):
            with self.assertRaises(ValueError, msg=bad):
                api.tile_to_grid(bad)


class DownloadUrlTest(unittest.TestCase):

    def test_url(self):
        self.assertEqual(
            api.tile_download_url('SU12NE'),
            'https://environment.data.gov.uk/tiles/collections/survey/lidar_composite_dtm/2022/1/SU1525')

    def test_http_errors(self):
        self.assertIn('no data', api.describe_http_error(500))
        self.assertIn('503', api.describe_http_error(503))


if __name__ == '__main__':
    unittest.main()


# ------------------------------------------------------------ search / datasets


FIXTURE = os.path.join(os.path.dirname(__file__), 'data', 'search_SU12NE.json')


def load_offerings():
    with open(FIXTURE, encoding='utf-8') as f:
        return api.parse_search_response(json.load(f))


class SearchTest(unittest.TestCase):

    def test_parse_keeps_lidar_and_aerial_photography(self):
        offerings = load_offerings()
        products = {o.dataset.product for o in offerings}
        self.assertIn('lidar_composite_dtm', products)
        self.assertIn('national_lidar_programme_point_cloud', products)
        self.assertIn('vertical_aerial_photography_tiles_irrgb', products)
        self.assertIn('oblique_aerial_photography_tiles_incident_response', products)
        self.assertTrue(all(o.tile == 'SU12NE' and o.tile_id == 'SU1525' for o in offerings))

    def test_parse_skips_other_products(self):
        data = {'results': [{'product': {'id': 'casi_multispectral_imagery'}, 'year': {'id': '2014'},
                             'resolution': {'id': '0.5'}, 'tile': {'id': 'TQ7070', 'label': 'TQ77sw'}, 'uri': 'u'}]}
        self.assertEqual(api.parse_search_response(data), [])

    def test_river_bathymetry(self):
        data = {'results': [{'product': {'id': 'bathymetry_riverine_multibeam'}, 'year': {'id': '2014'},
                             'resolution': {'id': '0.5'}, 'tile': {'id': 'TQ1065', 'label': 'TQ16nw'},
                             'uri': f'{api.BASE_URL}/bathymetry_riverine_multibeam/2014/0.5/TQ1065'}]}
        [offering] = api.parse_search_response(data)
        river = 'bathymetry_riverine_multibeam'
        self.assertEqual((offering.tile, offering.dataset), ('TQ16NW', api.Dataset(river, '2014', '0.5')))
        self.assertTrue(api.is_elevation(river) and api.product_kind(river) == api.RASTER)
        self.assertEqual(api.dataset_folder(offering.dataset),
                         os.path.join('bathymetry_riverine_multibeam', '0.5m', '2014'))
        # Listed with England's LiDAR, before the other nations and the imagery (the Processing tool's order)
        products = list(api.PRODUCTS)
        self.assertEqual(products[0], 'lidar_composite_dtm')
        self.assertLess(products.index('bathymetry_riverine_multibeam'), products.index('wales_lidar_dtm'))
        self.assertLess(products.index('scotland_lidar_point_cloud'),
                        products.index('vertical_aerial_photography_tiles_rgb'))
        order = sorted(api.PRODUCTS, key=api.product_sort_key)
        self.assertLess(order.index('bathymetry_riverine_multibeam'),
                        order.index('vertical_aerial_photography_tiles_rgb'))

    def test_imagery_kinds_and_order(self):
        self.assertEqual(api.product_kind('vertical_aerial_photography_tiles_rgbn'), api.IMAGERY)
        self.assertEqual(api.product_kind('oblique_aerial_photography_tiles_incident_response'), api.PHOTOS)
        self.assertEqual(api.product_kind('vertical_aerial_photography_tiles_new_kind'), api.IMAGERY)
        # Imagery sizes don't scale with resolution; LiDAR is listed before imagery
        irrgb = api.Dataset('vertical_aerial_photography_tiles_irrgb', '2014', '0.1')
        self.assertEqual(api.estimate_tile_mb(irrgb), 150)
        datasets = api.sorted_datasets({o.dataset for o in load_offerings()})
        kinds = [api.is_imagery(d.product) for d in datasets]
        self.assertEqual(kinds, sorted(kinds))
        self.assertTrue(kinds[-1])

    def test_urls_come_from_api(self):
        by_dataset = {o.dataset: o.url for o in load_offerings()}
        self.assertEqual(
            by_dataset[api.Dataset('lidar_point_cloud', '2012', 'NaN')],
            'https://environment.data.gov.uk/tiles/collections/survey/lidar_point_cloud/2012/NaN/SU1525')

    def test_summarise_restricts_to_selected_tiles(self):
        offerings = load_offerings()
        coverage = api.summarise(offerings, ['SU12NE', 'SU12NW'])
        self.assertEqual(coverage[api.DEFAULT_DATASET], {'SU12NE'})
        self.assertEqual(api.summarise(offerings, ['TQ77SW']), {})

    def test_bad_results_are_skipped(self):
        data = {'results': [{'product': {'id': 'lidar_composite_dtm'}}, None, {}]}
        self.assertEqual(api.parse_search_response(data), [])

    def test_sorted_datasets(self):
        datasets = api.sorted_datasets({o.dataset for o in load_offerings()})
        self.assertEqual(datasets[0], api.Dataset('lidar_composite_dtm', '2022', '1'))
        self.assertEqual(datasets[1], api.Dataset('lidar_composite_dtm', '2022', '2'))
        dtm_years = [d.year for d in datasets if d.product == 'lidar_tiles_dtm']
        self.assertEqual(dtm_years, sorted(dtm_years, reverse=True))


class DatasetTest(unittest.TestCase):

    def test_labels(self):
        self.assertEqual(api.resolution_label('0.5'), '0.5m')
        self.assertEqual(api.resolution_label('NaN'), 'N/A')
        self.assertEqual(api.dataset_label(api.Dataset('lidar_point_cloud', '2012', 'NaN')),
                         'England point cloud (individual surveys), 2012')
        self.assertEqual(api.product_label('lidar_something_new'), 'Lidar Something New')

    def test_folders(self):
        j = os.path.join
        self.assertEqual(api.dataset_folder(api.DEFAULT_DATASET), j('lidar_composite_dtm', '1m', '2022'))
        self.assertEqual(api.dataset_folder(api.Dataset('national_lidar_programme_dtm', '2019', '1')),
                         j('national_lidar_programme_dtm', '1m', '2019'))
        self.assertEqual(api.dataset_folder(api.Dataset('lidar_point_cloud', '2012', 'NaN')),
                         j('lidar_point_cloud', '2012'))
        self.assertEqual(api.dataset_folder(api.Dataset('lidar_tiles_dtm', 'latest', '0.5')),
                         j('lidar_tiles_dtm', '0.5m'))
        self.assertEqual(api.dataset_folder(api.Dataset('lidar_tiles_dtm', 'latest', '')), 'lidar_tiles_dtm')

    def test_folder_round_trip(self):
        for d in (api.DEFAULT_DATASET, api.Dataset('lidar_tiles_dtm', '2010', '0.5'),
                  api.Dataset('lidar_point_cloud', '2012', 'NaN')):
            parts = api.dataset_folder(d).split(os.sep)
            self.assertEqual(api.dataset_from_folder(parts[0], parts[1:]), d)
        self.assertIsNone(api.dataset_from_folder('lidar_tiles_dtm', ['notes']))

    def test_all_years_and_resolutions(self):
        whole = api.Dataset('lidar_tiles_dtm', api.LATEST, '')
        self.assertTrue(whole.is_all and not whole.is_concrete)
        self.assertEqual(api.dataset_label(whole), 'England DTM (individual surveys), newest for each tile')
        coverage = api.summarise(load_offerings(), ['SU12NE'])
        self.assertIn(whole, coverage)
        self.assertNotIn(api.Dataset('lidar_composite_dtm', api.LATEST, ''), api.summarise(
            [o for o in load_offerings() if o.dataset == api.DEFAULT_DATASET], ['SU12NE']))
        self.assertEqual(api.best_dataset([api.Dataset('x', '2010', '1'), api.Dataset('x', '2012', '2'),
                                           api.Dataset('x', '2012', '1')]), api.Dataset('x', '2012', '1'))

    def test_estimates(self):
        self.assertEqual(api.estimate_tile_mb(api.DEFAULT_DATASET), 70)
        self.assertAlmostEqual(api.estimate_tile_mb(api.Dataset('lidar_composite_dtm', '2022', '2')), 17.5)
        self.assertEqual(api.estimate_tile_mb(api.Dataset('national_lidar_programme_point_cloud', '2019', '1')), 285)
        self.assertEqual(api.estimate_tile_mb(api.Dataset('lidar_point_cloud', '2012', 'NaN')), 60)


class LatestTest(unittest.TestCase):

    def offering(self, product, year, tile):
        return api.Offering(api.Dataset(product, year, '1'), tile, api.tile_to_grid(tile), f'u/{product}/{year}/{tile}')

    def test_latest_picks_newest_per_tile(self):
        nlp = 'national_lidar_programme_dtm'
        offerings = [self.offering(nlp, '2018', 'NY46NW'), self.offering(nlp, '2020', 'NY46NW'),
                     self.offering(nlp, '2018', 'NY46NE')]
        latest = api.Dataset(nlp, api.LATEST, '1')
        coverage = api.summarise(offerings, ['NY46NW', 'NY46NE'])
        self.assertEqual(coverage[latest], {'NY46NW', 'NY46NE'})
        self.assertEqual(coverage[api.Dataset(nlp, '2020', '1')], {'NY46NW'})
        self.assertEqual(api.pick_offering(offerings[:2], latest).dataset.year, '2020')
        self.assertEqual(api.pick_offering(offerings[2:], latest).dataset.year, '2018')
        self.assertIsNone(api.pick_offering(offerings, api.Dataset('lidar_composite_dtm', api.LATEST, '1')))
        self.assertEqual(api.dataset_label(latest), 'England National LIDAR Programme DTM, newest for each tile, 1 m')
        self.assertEqual(api.dataset_folder(latest), os.path.join('national_lidar_programme_dtm', '1m'))
        ordered = api.sorted_datasets(coverage)
        self.assertEqual([d.year for d in ordered], ['latest', 'latest', '2020', '2018'])  # 1m latest + all

    def test_single_year_products_get_no_latest(self):
        offerings = load_offerings()  # SU12NE: composite products have one year
        coverage = api.summarise(offerings, ['SU12NE'])
        self.assertNotIn(api.Dataset('lidar_composite_dtm', api.LATEST, '1'), coverage)
        self.assertIn(api.Dataset('lidar_tiles_dtm', api.LATEST, '1'), coverage)  # 2005/2006/2012 at 1m


class FinestResolutionTest(unittest.TestCase):

    def test_blank_resolution_picks_newest_then_finest(self):
        def offering(year, res):
            return api.Offering(api.Dataset('lidar_tiles_dtm', year, res), 'SU12NE', 'SU1525', f'u/{year}/{res}')
        offerings = [offering('2010', '1'), offering('2010', '0.5'), offering('2012', '2'), offering('2012', '1')]
        self.assertEqual(api.pick_offering(offerings, api.Dataset('lidar_tiles_dtm', 'latest', '')).url, 'u/2012/1')
        self.assertEqual(api.pick_offering(offerings, api.Dataset('lidar_tiles_dtm', '2010', '')).url, 'u/2010/0.5')
        self.assertEqual(api.pick_offering(offerings, api.Dataset('lidar_tiles_dtm', 'latest', '0.5')).url,
                         'u/2010/0.5')
        self.assertIsNone(api.pick_offering(offerings, api.Dataset('lidar_tiles_dtm', '2011', '')))
        self.assertEqual(api.dataset_label(api.Dataset('lidar_tiles_dtm', 'latest', '')),
                         'England DTM (individual surveys), newest for each tile')
        self.assertEqual(api.dataset_label(api.Dataset('lidar_tiles_dtm', '2010', '')),
                         'England DTM (individual surveys), 2010, finest resolution')


class GroupingTest(unittest.TestCase):

    def test_tree_products_then_resolution_then_year(self):
        coverage = api.summarise(load_offerings(), ['SU12NE'])
        tree = api.dataset_tree(coverage)
        by_label = {label: children for label, children in tree}
        # One nation: a node per product at the top level
        self.assertEqual(len(tree), len({d.product for d in coverage}))
        self.assertIn('England Composite DTM', by_label)
        # Several surveys: "newest for each tile" first, then direct choices (year, then resolution)
        self.assertEqual([label for label, _ in by_label['England Composite DTM']],
                         ['newest for each tile', '2022, 1 m', '2022, 2 m'])
        # Several resolutions, several years at 1 m: a sub-menu for 1 m ("newest" first, newest year next)
        tiles_dtm = dict(by_label['England DTM (individual surveys)'])
        self.assertEqual([label for label, _ in tiles_dtm['1 m']], ['newest for each tile', '2012', '2006', '2005'])
        self.assertIsInstance(tiles_dtm['2003, 2 m'], api.Dataset)
        # One "resolution" (none, for point clouds) with several years: years listed directly
        clouds = [label for label, _ in by_label['England point cloud (individual surveys)']]
        self.assertEqual(clouds[0], 'newest for each tile')
        self.assertEqual(clouds.count('newest for each tile'), 1)  # not twice for a single resolution
        # A product with a single choice is that choice (no submenu of one)
        self.assertIsInstance(by_label['England National LIDAR Programme DTM, 2019, 1 m'], api.Dataset)
        # Imagery products come after all LiDAR products
        imagery = [api.is_imagery(api.tree_datasets(node)[0].product) for node in tree]
        self.assertEqual(imagery, sorted(imagery))
        # Everything is in the menu except "newest" per resolution for single-resolution products (= the product's)
        listed = set(api.tree_datasets(('all', tree)))
        omitted = set(coverage) - listed
        self.assertTrue(listed <= set(coverage))
        self.assertTrue(omitted and all(d.is_latest and d.resolution for d in omitted))

    def test_tree_across_a_border(self):
        england = api.Dataset('lidar_composite_dtm', '2022', '1')
        wales = api.Dataset('wales_lidar_dtm', '2020-2022', '1')
        photos = api.Dataset('vertical_aerial_photography_tiles_rgb', '2014', '0.2')
        tree = api.dataset_tree([england, wales, photos])
        # One submenu per nation, products named without it; England's photos in a submenu of their own
        self.assertEqual([label for label, _ in tree], ['England', 'Wales', 'England aerial photos'])
        self.assertEqual(tree[0][1], [('Composite DTM, 2022, 1 m', england)])
        self.assertEqual(tree[1][1], [('DTM (national survey), 2020-22, 1 m', wales)])
        self.assertEqual(api.dataset_label(wales), 'Wales DTM (national survey), 2020-22, 1 m')
        self.assertEqual(api.dataset_label(wales, within_nation=True), 'DTM (national survey), 2020-22, 1 m')

    def test_sizes(self):
        ref = api.FileRef('https://srsp-open-data.s3.eu-west-2.amazonaws.com/x/NS79.tif', 'NS79.tif', 'NS79', 6_200_000)
        scottish = api.Offering(api.Dataset('scotland_lidar_dtm', '2011-phase-1', '1'), 'NS79NE', 'NS79NE', '',
                                (ref,))
        counted = set()
        self.assertEqual(round(api.download_mb(scottish, counted), 1), 6.2)
        self.assertEqual(api.download_mb(scottish, counted), 0)  # the same 10 km file for another tile: counted once
        welsh = api.Offering(api.Dataset('wales_lidar_dtm', '2020-2022', '1'), 'SH28SW', 'SH28SW', '', tuple(
            api.FileRef(f'https://dmwproductionblob.blob.core.windows.net/lidar-zips/{n}.tif', f'{n}.tif', 'SH28SW')
            for n in range(22)))
        self.assertAlmostEqual(api.download_mb(welsh), 22 * 1.45)
        archive_2m = api.Offering(api.Dataset('wales_lidar_archive_dtm', '1998', '2'), 'ST16NE', 'ST16NE', '', (
            api.FileRef('https://lle.blob.core.windows.net/lidar/2m_res_ST16_1998_dtm.zip', 'a.zip', 'ST16'),))
        self.assertAlmostEqual(api.download_mb(archive_2m), 42 / 4)
        england = api.Offering(api.DEFAULT_DATASET, 'SU12NE', 'SU1525', f'{api.BASE_URL}/x')
        self.assertEqual(api.download_mb(england), 70)
        self.assertEqual((api.disk_mb(70, True), api.disk_mb(70, True, True), api.disk_mb(32, False)), (140, 70, 32))

    def test_parse_resolution(self):
        for text, expected in (('1', '1'), ('1.0', '1'), (' 1 m', '1'), ('50cm', '0.5'), ('0.5m', '0.5'),
                               ('2 M', '2'), ('', ''), (None, '')):
            self.assertEqual(api.parse_resolution(text), expected, text)
        for text in ('fine', '-1', '0'):
            with self.assertRaises(ValueError):
                api.parse_resolution(text)

    def test_dataset_key(self):
        self.assertEqual(api.dataset_key(api.DEFAULT_DATASET), 'lidar_composite_dtm|2022|1')

    def test_descriptions_and_attribution(self):
        self.assertTrue(all(api.product_description(p) for p in api.PRODUCTS))
        self.assertTrue(api.is_elevation('lidar_composite_dtm'))
        self.assertFalse(api.is_elevation('national_lidar_programme_vom'))
        self.assertEqual(api.attribution(api.DEFAULT_DATASET),
                         '© Environment Agency copyright and/or database right 2022. All rights reserved.')
        self.assertNotIn('latest', api.attribution(api.Dataset('lidar_tiles_dtm', api.LATEST, '')))

    def test_survey_rank(self):
        old, new_2m, new_1m = (api.Dataset('lidar_tiles_dtm', '2009', '1'), api.Dataset('lidar_tiles_dtm', '2017', '2'),
                               api.Dataset('lidar_tiles_dtm', '2017', '1'))
        # Mosaics list surveys worst first, best last (drawn on top)
        self.assertEqual(sorted([new_1m, old, new_2m], key=api.survey_rank), [old, new_2m, new_1m])
        self.assertEqual(api.best_dataset([new_1m, old, new_2m]), new_1m)

    def test_only_trusted_links_and_real_tiles(self):
        def result(label, uri):
            return {'product': {'id': 'lidar_composite_dtm'}, 'year': {'id': '2022'}, 'resolution': {'id': '1'},
                    'tile': {'id': 'SU1525', 'label': label}, 'uri': uri}
        good = f'{api.BASE_URL}/lidar_composite_dtm/2022/1/SU1525'
        data = {'results': [result('SU12ne', good), result('SU12ne', 'https://example.com/SU1525.zip'),
                            result('SU12ne', 'http://environment.data.gov.uk/tiles/collections/survey/x'),
                            result('SU12ne', api.BASE_URL + '/../../elsewhere'), result('../../evil', good),
                            result('SU12NEX', good), result(None, good)]}
        self.assertEqual([(o.tile, o.url) for o in api.parse_search_response(data)], [('SU12NE', good)])
        self.assertTrue(api.TILE_RE.match('TQ16NW') and not api.TILE_RE.match('TI16NW'))


class WhereTest(unittest.TestCase):
    def test_squares_text(self):
        self.assertEqual(api.squares_text(['SU12NE', 'SU12NW', 'SU22NW']), 'SU12, SU22')
        self.assertEqual(api.squares_text(['SU12NE', 'SU22NW', 'SU32NW', 'SU42NW', 'TQ09SW', 'ST16NE']),
                         'ST16, SU12, SU22, SU32 +2 more')
