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

import abc
import argparse
import concurrent.futures
import csv
import itertools
import os
import sys
import types
import typing
import warnings
import xml.etree.ElementTree

if typing.TYPE_CHECKING:
    import rasterio
    import rasterio.errors
    import rasterio.warp
    _STRATEGIES = ('rasterio', 'diy')
else:
    try:
        import rasterio
        import rasterio.errors
        import rasterio.warp
        _STRATEGIES = ('rasterio', 'diy')
    except ImportError:
        _STRATEGIES = ('diy',)

import faa_tpp_iap_georef_diy
from faa_tpp_iap_georef_types import ChartGeorefInfo, LatLon

_T = typing.TypeVar("_T")
_P = typing.ParamSpec("_P")


class _ImmediateExecutor(concurrent.futures.Executor):

    def __init__(self, max_workers: int | None = None):
        if max_workers is not None and max_workers != 1:
            raise ValueError(type(self).__name__ + ' only supports 1 worker')

    @typing.override
    def submit(
        self,
        fn: typing.Callable[_P, _T],
        /,
        *args: _P.args,
        **kwargs: _P.kwargs,
    ) -> concurrent.futures.Future[_T]:
        future: concurrent.futures.Future[_T] = concurrent.futures.Future()

        if future.set_running_or_notify_cancel():
            try:
                future.set_result(fn(*args, **kwargs))
            except BaseException as e:
                future.set_exception(e)

        return future


def faa_tpp_iap_georef_chart_rasterio(
        pdf_path: os.PathLike[str] | str) -> ChartGeorefInfo | None:
    with warnings.catch_warnings(), rasterio.Env(GDAL_PDF_DPI=300):
        # Allow non-georeferenced plates to be skipped.
        warnings.simplefilter('error', rasterio.errors.NotGeoreferencedWarning)

        try:
            with rasterio.open(pdf_path) as rio:
                crs_dict = rio.crs.to_dict()

                assert crs_dict['proj'] == 'lcc'
                assert crs_dict['x_0'] == 0
                assert crs_dict['y_0'] == 0
                assert crs_dict['datum'] == 'NAD83'
                assert crs_dict['units'] == 'us-in'
                assert crs_dict['no_defs']  # no defaults were used

                # Corner coordinates. “xy” are Cartesian, distances from the
                # origin.
                xy_tl = rio.xy(0, 0, offset='ul')
                xy_bl = rio.xy(rio.height - 1, 0, offset='ll')
                xy_tr = rio.xy(0, rio.width - 1, offset='ur')
                xy_br = rio.xy(rio.height - 1, rio.width - 1, offset='lr')

                # Corner coordinates. “ll” are geographic (latitude/longitude).
                ll_wgs84 = rasterio.warp.transform(
                    rio.crs,
                    crs_dict['datum'],  # 'NAD83', or 'WGS84' or 'EPSG:4326'
                    (xy_tl[0], xy_bl[0], xy_tr[0], xy_br[0]),
                    (xy_tl[1], xy_bl[1], xy_tr[1], xy_br[1]))
                ll_tl, ll_bl, ll_tr, ll_br = (
                    LatLon(ll_wgs84[1][i], ll_wgs84[0][i]) for i in range(4))

                return ChartGeorefInfo(
                    os.path.basename(pdf_path),
                    crs_dict,
                    rio.crs.to_wkt(version='WKT1_ESRI'),
                    crs_dict['lat_1'],
                    crs_dict['lat_2'],
                    LatLon(crs_dict['lat_0'], crs_dict['lon_0']),
                    {
                        (0.0, 1.0): ll_tl,
                        (0.0, 0.0): ll_bl,
                        (1.0, 1.0): ll_tr,
                        (1.0, 0.0): ll_br
                    },
                )

        except rasterio.errors.NotGeoreferencedWarning:
            return None


# precision = 7 gives .00036″ resolution (better than dd°mm′ss.sss″). This is
# ≤1.1cm on either axis.
#
# precision > 6 does cause small discrepancies between the rasterio and diy
# strategies, although at precision = 7, this only occurs on 5 charts as of
# cycle 2608.
def _f_p(f: float, precision: int | None) -> str:
    if precision is None or precision == 0:
        return str(f)

    return ('%%.%uf' % precision) % f


class _FaaTppIapGeorefOutputInterface(abc.ABC):

    @abc.abstractmethod
    def __init__(self,
                 metafile: xml.etree.ElementTree.ElementTree[
                     xml.etree.ElementTree.Element[str]],
                 csv_file: typing.TextIO,
                 *,
                 precision: int | None = None):
        ...

    def __enter__(self) -> _FaaTppIapGeorefOutputInterface:
        return self

    @abc.abstractmethod
    def add_chart(
        self,
        chart_el: xml.etree.ElementTree.Element,
        georef_info: ChartGeorefInfo,
    ) -> None:
        ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: types.TracebackType | None,
    ) -> None:
        pass


class _FaaTppIapGeorefCsvOutput(_FaaTppIapGeorefOutputInterface):
    __slots__ = ('_csv_writer', '_precision')

    def __init__(self,
                 metafile: xml.etree.ElementTree.ElementTree[
                     xml.etree.ElementTree.Element[str]],
                 csv_file: typing.TextIO,
                 *,
                 precision: int | None = None):
        self._csv_writer = csv.writer(csv_file, lineterminator='\n')
        self._precision = precision

    @typing.override
    def __enter__(self) -> _FaaTppIapGeorefCsvOutput:
        # Write the CSV header.
        self._csv_writer.writerow((
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

        return self

    @typing.override
    def add_chart(
        self,
        chart_el: xml.etree.ElementTree.Element,
        georef_info: ChartGeorefInfo,
    ) -> None:
        # Write this chart’s information to the CSV output.
        self._csv_writer.writerow((
            georef_info.pdf_name,
            *(float(f) for f in (
                georef_info.sp_lat_1,
                georef_info.sp_lat_2,
                *georef_info.origin,
            )),
            *(_f_p(f, self._precision) for f in (
                *georef_info.control_points[0.0, 1.0],
                *georef_info.control_points[0.0, 0.0],
                *georef_info.control_points[1.0, 1.0],
                *georef_info.control_points[1.0, 0.0],
            )),
        ))


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
        georef_info: ChartGeorefInfo,
        *,
        precision: int | None = None) -> xml.etree.ElementTree.Element:
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
                    text=georef_info.crs_wkt),
            _xml_el(
                'projection',
                attrib={
                    'type': georef_info.crs_dict['proj'],
                    'datum': georef_info.crs_dict['datum']
                },
                children=(
                    _xml_el(
                        'standard_parallel',
                        attrib={'latitude': str(float(georef_info.sp_lat_1))}),
                    _xml_el(
                        'standard_parallel',
                        attrib={'latitude': str(float(georef_info.sp_lat_2))}),
                    _xml_el('origin',
                            attrib={
                                'latitude': str(float(georef_info.origin.lat)),
                                'longitude': str(float(georef_info.origin.lon))
                            }),
                )),
            _xml_el(
                'control_points',
                children=(_xml_el('control_point',
                                  attrib={
                                      'latitude': _f_p(ll.lat, precision),
                                      'longitude': _f_p(ll.lon, precision),
                                      'x': str(x),
                                      'y': str(y)
                                  })
                          for (x, y), ll in georef_info.control_points.items()),
            ),
        ))


class _FaaTppIapGeorefXmlOutput(_FaaTppIapGeorefOutputInterface):
    __slots__ = ('_metafile', '_xml_file', '_precision')

    def __init__(self,
                 metafile: xml.etree.ElementTree.ElementTree[
                     xml.etree.ElementTree.Element[str]],
                 xml_file: typing.TextIO,
                 *,
                 precision: int | None = None):
        self._metafile = metafile
        self._xml_file = xml_file
        self._precision = precision

    @typing.override
    def add_chart(
        self,
        chart_el: xml.etree.ElementTree.Element,
        georef_info: ChartGeorefInfo,
    ) -> None:
        # Insert this chart’s information into the XML structure.
        chart_el.append(
            _georeferencing_el(georef_info, precision=self._precision))

    @typing.override
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: types.TracebackType | None,
    ) -> None:
        # Write the XML output.
        xml.etree.ElementTree.indent(self._metafile)
        self._metafile.write(self._xml_file,
                             encoding='unicode',
                             xml_declaration=True,
                             short_empty_elements=False)


def _chart_el_to_pdf_name(chart_el: xml.etree.ElementTree.Element[str]) -> str:
    pdf_name_el, = chart_el.iterfind('./pdf_name')
    pdf_name = pdf_name_el.text
    assert isinstance(pdf_name, str)
    return pdf_name


def faa_tpp_iap_georef(
    tpp_dir: os.PathLike[str] | str,
    georef_chart_f: typing.Callable[[os.PathLike[str] | str],
                                    ChartGeorefInfo | None],
    output_cls: type[_FaaTppIapGeorefOutputInterface],
    output_file: typing.TextIO,
    *,
    precision: int | None = None,
    parallel: int | None = 1,
) -> None:
    metafile = xml.etree.ElementTree.parse(
        os.path.join(tpp_dir, 'd-TPP_Metafile.xml'))
    assert metafile.getroot().tag == 'digital_tpp'

    # Scan d-TPP_Metafile.xml for charts. Only look at IAPs, and don’t look at
    # anything that’s been deleted.
    chart_els_gen = metafile.iterfind(
        './state_code/city_name/airport_name/record/'
        'chart_code[.="IAP"]/../useraction[.!="D"]/..')
    chart_els_gen_tee = itertools.tee(chart_els_gen, 2)
    pdf_names_gen = (
        _chart_el_to_pdf_name(chart_el) for chart_el in chart_els_gen_tee[0])

    executor_cls = (_ImmediateExecutor if parallel == 1 else
                    concurrent.futures.ProcessPoolExecutor)
    with (
            executor_cls(max_workers=parallel) as executor,
            output_cls(metafile, output_file, precision=precision) as output,
    ):
        for chart_el, georef_info in zip(
                chart_els_gen_tee[1],
                executor.map(georef_chart_f, (os.path.join(tpp_dir, pdf_name)
                                              for pdf_name in pdf_names_gen),
                             chunksize=16)):
            if georef_info is None:
                continue

            output.add_chart(chart_el, georef_info)


def main(args: typing.Sequence[str]) -> int | None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--parallel', type=int)
    parser.add_argument('--strategy',
                        choices=_STRATEGIES,
                        default=_STRATEGIES[0])
    parser.add_argument('--format', choices=('xml', 'csv'), default='xml')
    parser.add_argument('--precision', type=int, default=7)
    parser.add_argument('tpp_dir')
    parser.add_argument('out_path', nargs='?')
    parsed = parser.parse_args(args)

    georef_chart_f = {
        'rasterio': faa_tpp_iap_georef_chart_rasterio,
        'diy': faa_tpp_iap_georef_diy.faa_tpp_iap_georef_chart_diy,
    }[parsed.strategy]

    output_cls = {
        'xml': _FaaTppIapGeorefXmlOutput,
        'csv': _FaaTppIapGeorefCsvOutput,
    }[parsed.format]

    if parsed.out_path is None:
        faa_tpp_iap_georef(parsed.tpp_dir,
                           georef_chart_f,
                           output_cls,
                           sys.stdout,
                           precision=parsed.precision,
                           parallel=parsed.parallel)
    else:
        with open(parsed.out_path, 'w', newline='\r\n') as out_file:
            faa_tpp_iap_georef(parsed.tpp_dir,
                               georef_chart_f,
                               output_cls,
                               out_file,
                               precision=parsed.precision,
                               parallel=parsed.parallel)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
