# Contributing to LIDAR Downloader UK

Thank you for helping. Bug reports, ideas and code are all welcome. This guide explains how to take part and what happens to your contribution.

LIDAR Downloader UK is maintained by Simon Stoate, who decides what goes into the plugin and when it's released. Suggestions that don't fit the plugin's direction may be declined, but they'll always get a reply.

## Reporting a bug

Open an [issue](https://github.com/simonstoate/LidarDownloaderUK/issues) and include:

- your QGIS version (**Help → About**) and operating system
- the plugin version (**Plugins → Manage and Install Plugins**)
- what you did, what you expected, and what happened instead
- the tile names and dataset involved (e.g. *SU12NE, England Composite DTM, 2022, 1 m*), if it's about a download
- any messages from **View → Panels → Log Messages → LIDAR Downloader UK**

If a download service seems to have changed (datasets missing, downloads failing everywhere), please say which nation it affects: England (Environment Agency), Wales (DataMapWales) or Scotland (Scottish Remote Sensing Portal).

## Suggesting a feature

Open an issue describing the problem you're trying to solve, not only the solution: *"I need to know which surveys cover my site before I download"* helps more than *"add a survey list"*. The plugin is meant to be useful to everyone who works with this data, so features are designed to be general, with options for specialist needs. It sticks to finding, downloading, organising and loading the data: analysis (styling, terrain and contour exports, catchments) belongs in a separate companion plugin.

## Contributing code

For anything more than a small fix, **open an issue first** so we can agree the approach before you spend time on it.

1. Fork the repository and create a branch from `main`.
2. Make your change, with tests (see below).
3. Check that lint, the unit tests and the QGIS smoke test pass.
4. Open a pull request explaining what it changes and why, and link the issue.

Pull requests are reviewed as time allows. You may be asked for changes. A pull request may also be declined, or its idea implemented differently.

### Setting up

See [Development](README.md#development) in the README for the code layout. In short:

```
pip install pytest flake8 bandit detect-secrets
python -m pytest
flake8 lidar_downloader_uk tests tools
bandit -r lidar_downloader_uk
detect-secrets scan lidar_downloader_uk
```

plugins.qgis.org scans every upload with Bandit and detect-secrets and won't publish a version with findings, so both must stay clean.

The smoke test runs the whole plugin headless inside QGIS, with a faked network:

```
"C:\Program Files\QGIS 4.2.1\bin\python-qgis.bat" "<repo>\tests\smoke_test_qgis.py"
```

GitHub Actions runs all of these on QGIS 3.22, 3.34, 3.40, 3.44 (Qt5) and 4.2 (Qt6) for every pull request.

### Code guidelines

- **One codebase for QGIS 3 and QGIS 4.** Follow the compatibility rules in the README: import Qt only through `qgis.PyQt`, use fully scoped enums, `exec()` not `exec_()`, and guard APIs newer than QGIS 3.22.
- **Background work.** Code in a task's `run()` runs on a worker thread, so it must not touch widgets or the project. Don't connect lambdas to signals from worker threads (this crashed QGIS 3); connect to real methods.
- **Network.** Use QGIS's network classes (`QgsBlockingNetworkRequest` / `QgsNetworkAccessManager`) so users' proxy and authentication settings apply. Only download from the official services listed in `api.py`. Don't scrape unofficial copies of the data.
- **Files.** Never delete or overwrite files the plugin didn't create. Anything that deletes asks the user first.
- **Keep pure logic testable.** `api.py`, `sources.py`, `storage.py`, `places.py` and `migrate.py` don't import QGIS, so they can be unit-tested with plain pytest. Keep them that way.
- **Wording.** User-facing text is in British English, writes LIDAR in capitals (as the Environment Agency and gov.uk do), and explains problems in plain language.
- **Data and licences.** Don't add bundled data unless its licence allows redistribution. Credit it in `lidar_downloader_uk/data/LICENSE-DATA.txt`.
- **New files** start with the two SPDX licence lines the others have, then a docstring saying what the module is for.
- Match the style of the surrounding code, including its comments: docstrings say what a function gives back or does, comments say *why* where the code can't. Lines are at most 120 characters.

## Licensing of contributions

The plugin is licensed under the [GNU General Public License v2.0 or later](LICENSE).

By submitting a contribution (code, documentation or other material) you confirm that:

1. **You have the right to submit it.** You wrote it yourself, or you have permission to contribute it under these terms, and it doesn't include anyone else's code under an incompatible licence.
2. **You license it under GPL-2.0-or-later**, like the rest of the project.

You keep the copyright in your contribution. Please add a `Signed-off-by: Your Name <email>` line to your commits (`git commit -s`) to confirm the above.

## Behaviour

Be respectful and constructive in issues and pull requests. Assume good intent, keep discussion about the work, and remember that everyone here is giving their own time. Harassment or abuse isn't tolerated, and comments or contributors may be removed.

## Questions

For anything else, open an issue.
