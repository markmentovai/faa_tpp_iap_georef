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
# It is limited to XML output.

import argparse
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


def _xml_el(
    tag: str,
    attrib: dict[str, str] = {},
    text: str | None = None,
    children: typing.Iterable[xml.etree.ElementTree.Element] = ()
) -> xml.etree.ElementTree.Element:
    el = xml.etree.ElementTree.Element(tag, attrib=attrib)
    el.text = text
    el.extend(children)
    return el


def _georeferencing_el(
    crs_dict: dict[str, typing.Any],
    wkt: str,
    ll_tl: tuple[float, float],
    ll_bl: tuple[float, float],
    ll_tr: tuple[float, float],
    ll_br: tuple[float, float],
) -> xml.etree.ElementTree.Element:
    # This only has one caller, but it’s broken into its own function to reduce
    # the indentation at the point of use, making it more readable.
    return _xml_el(
        'georeferencing',
        children=(
            _xml_el('coordinate_reference_system',
                    attrib={
                        'format': 'wkt',
                        'version': 'WKT1_ESRI'
                    },
                    text=wkt),
            _xml_el(
                'projection',
                attrib={
                    'type': crs_dict['proj'],
                    'datum': crs_dict['datum']
                },
                children=(
                    _xml_el('standard_parallel',
                            attrib={'latitude': str(float(crs_dict['lat_1']))}),
                    _xml_el('standard_parallel',
                            attrib={'latitude': str(float(crs_dict['lat_2']))}),
                    _xml_el('origin',
                            attrib={
                                'latitude': str(float(crs_dict['lat_0'])),
                                'longitude': str(float(crs_dict['lon_0']))
                            }),
                )),
            _xml_el(
                'control_points',
                children=(
                    _xml_el(
                        'control_point',
                        attrib={
                            # 7 decimal places is .00036″ resolution, better
                            # than dd°mm′ss.sss″, ≤1.1cm.
                            'latitude': f'{lat:.7f}',
                            'longitude': f'{lon:.7f}',
                            'x': str(float(x)),
                            'y': str(float(y))
                        }) for (lat, lon), (x, y) in zip(
                            (ll_tl, ll_bl, ll_tr, ll_br),
                            ((0, 1), (0, 0), (1, 1), (1, 0)),
                        )),
            ),
        ))


def faa_tpp_iap_georef_xml(in_path: os.PathLike[str] | str,
                           xml_out_file: typing.TextIO) -> None:
    try:
        tpp_dir = in_path
        metafile = xml.etree.ElementTree.parse(
            os.path.join(in_path, 'd-TPP_Metafile.xml'))
    except NotADirectoryError, FileNotFoundError:
        tpp_dir = os.path.dirname(in_path)
        metafile = xml.etree.ElementTree.parse(in_path)

    DataError.raise_if_ne(metafile.getroot().tag, 'digital_tpp')

    # Scan d-TPP_Metafile.xml for charts. Only look at IAPs, and don’t look at
    # anything that’s been deleted.
    for chart_el in metafile.iterfind(
            './state_code/city_name/airport_name/record/chart_code[.="IAP"]/' +
            '../useraction[.!="D"]/..'):
        pdf_name_el, = chart_el.iterfind('./pdf_name')
        pdf_name = pdf_name_el.text
        assert isinstance(pdf_name, str)

        # Remove stale georeferencing elements.
        for old_georeferencing_el in chart_el.iterfind('georeferencing'):
            chart_el.remove(old_georeferencing_el)

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

                    # Insert this chart’s information into the XML structure.
                    chart_el.append(
                        _georeferencing_el(crs_dict,
                                           rio.crs.to_wkt(version='WKT1_ESRI'),
                                           ll_tl, ll_bl, ll_tr, ll_br))

            except rasterio.errors.NotGeoreferencedWarning:
                pass

    # Write the XML output.
    xml.etree.ElementTree.indent(metafile)
    metafile.write(xml_out_file,
                   encoding='unicode',
                   xml_declaration=True,
                   short_empty_elements=False)


def main(args: typing.Sequence[str]) -> int | None:
    parser = argparse.ArgumentParser()
    parser.add_argument('in_path')
    parser.add_argument('xml_out_path', nargs='?')
    parsed = parser.parse_args(args)

    if parsed.xml_out_path is None:
        faa_tpp_iap_georef_xml(parsed.in_path, sys.stdout)
    else:
        with open(parsed.xml_out_path, 'w', newline='\r\n') as xml_out_file:
            faa_tpp_iap_georef_xml(parsed.in_path, xml_out_file)

    return None


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
