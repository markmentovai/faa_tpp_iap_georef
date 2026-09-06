#!/usr/bin/env python3

# Copyright 2026 Mark Mentovai
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# pyright: strict

# This is scaled-back version of faa_tpp_iap_georef for demonstration purposes.
# It is limited to CSV output.

import argparse
import collections.abc
import csv
import typing
import os
import sys
import warnings
import xml.etree.ElementTree

import rasterio
import rasterio.errors
import rasterio.warp


class DataError(Exception):

    @classmethod
    def raise_if_false(cls, value: typing.Any) -> None:
        if not value:
            raise cls(f'not {value}')

    @classmethod
    def raise_if_ne(cls, a: typing.Any, b: typing.Any) -> None:
        if a != b:
            raise cls(f'{a} != {b}')


def faa_tpp_iap_georef_csv(in_path: os.PathLike[str] | str,
                           csv_out_file: typing.TextIO) -> None:
    try:
        tpp_dir = in_path
        metafile = xml.etree.ElementTree.parse(
            os.path.join(in_path, 'd-TPP_Metafile.xml'))
    except NotADirectoryError, FileNotFoundError:
        tpp_dir = os.path.dirname(in_path)
        metafile = xml.etree.ElementTree.parse(in_path)

    DataError.raise_if_ne(metafile.getroot().tag, 'digital_tpp')

    csv_writer = csv.writer(csv_out_file, lineterminator='\n')

    # Write the CSV header.
    csv_writer.writerow((
        'filename',
        'sp_lat_1',
        'sp_lat_2',
        'origin_lat',
        'origin_lon',
        'tl_lat',
        'tl_lon',
        'bl_lat',
        'bl_lon',
        'tr_lat',
        'tr_lon',
        'br_lat',
        'br_lon',
    ))

    # Scan d-TPP_Metafile.xml for charts. Only look at IAPs, and don’t look at
    # anything that’s been deleted.
    for chart_el in metafile.iterfind(
            './state_code/city_name/airport_name/record/chart_code[.="IAP"]/' +
            '../useraction[.!="D"]/..'):
        pdf_name_el, = chart_el.iterfind('./pdf_name')
        pdf_name = pdf_name_el.text
        assert isinstance(pdf_name, str)

        with warnings.catch_warnings(), rasterio.Env(GDAL_PDF_DPI=300):
            # Allow non-georeferenced plates to be skipped.
            warnings.simplefilter('error',
                                  rasterio.errors.NotGeoreferencedWarning)

            try:
                with rasterio.open(os.path.join(tpp_dir, pdf_name)) as rio:
                    crs_dict = rio.crs.to_dict()

                    DataError.raise_if_ne(crs_dict['proj'], 'lcc')
                    DataError.raise_if_ne(crs_dict['x_0'], 0)
                    DataError.raise_if_ne(crs_dict['y_0'], 0)
                    DataError.raise_if_ne(crs_dict['datum'], 'NAD83')
                    DataError.raise_if_ne(crs_dict['units'], 'us-in')
                    DataError.raise_if_false(
                        crs_dict['no_defs'])  # no defaults were used

                    # Corner coordinates. “xy” are Cartesian, distances from the
                    # origin.
                    xy_tl = rio.xy(0, 0, offset='ul')
                    xy_bl = rio.xy(rio.height - 1, 0, offset='ll')
                    xy_tr = rio.xy(0, rio.width - 1, offset='ur')
                    xy_br = rio.xy(rio.height - 1, rio.width - 1, offset='lr')

                    # Corner coordinates. “ll” are geographic
                    # (latitude/longitude).
                    ll_wgs84 = rasterio.warp.transform(
                        rio.crs,
                        crs_dict['datum'],  # 'NAD83', or 'WGS84' or 'EPSG:4326'
                        (xy_tl[0], xy_bl[0], xy_tr[0], xy_br[0]),
                        (xy_tl[1], xy_bl[1], xy_tr[1], xy_br[1]))
                    ll_tl, ll_bl, ll_tr, ll_br = (
                        (ll_wgs84[1][i], ll_wgs84[0][i]) for i in range(4))

                    # Write this chart’s information to the CSV output. 7
                    # decimal places is .00036″ resolution, better than
                    # dd°mm′ss.sss″, ≤1.1cm.
                    csv_writer.writerow((
                        pdf_name,
                        *(float(crs_dict[k]) for k in (
                            'lat_1',
                            'lat_2',
                            'lat_0',
                            'lon_0',
                        )),
                        *(f'{f:.7f}' for f in (
                            *ll_tl,
                            *ll_bl,
                            *ll_tr,
                            *ll_br,
                        )),
                    ))
            except rasterio.errors.NotGeoreferencedWarning:
                pass


def main(args: collections.abc.Sequence[str]) -> int | None:
    parser = argparse.ArgumentParser()
    parser.add_argument('in_path')
    parser.add_argument('csv_out_path', nargs='?')
    parsed = parser.parse_args(args)

    if parsed.csv_out_path is None:
        faa_tpp_iap_georef_csv(parsed.in_path, sys.stdout)
    else:
        with open(parsed.csv_out_path, 'w', newline='\r\n') as csv_out_file:
            faa_tpp_iap_georef_csv(parsed.in_path, csv_out_file)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
