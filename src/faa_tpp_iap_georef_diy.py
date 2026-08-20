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

import decimal
import math
import os
import re
import statistics
import warnings

import pikepdf

from faa_tpp_iap_georef_types import ChartGeorefInfo, LatLon
import lambert_conformal_conic


class Rect:
    __slots__ = ('_x', '_y', '_w', '_h')

    def __init__(self, x: float, y: float, w: float, h: float):
        self._x = x
        self._y = y
        self._w = w
        self._h = h

    @classmethod
    def from_xywh(cls, x: float, y: float, w: float, h: float) -> Rect:
        return cls(x, y, w, h)

    @classmethod
    def from_lbrt(cls, l: float, b: float, r: float, t: float) -> Rect:
        return cls(l, b, r - l, t - b)

    @classmethod
    def from_pdf_box_array(cls, pdf_box_array: pikepdf.Array) -> Rect:
        l, b, r, t = pdf_box_array
        assert isinstance(l, (decimal.Decimal, float, int))
        assert isinstance(b, (decimal.Decimal, float, int))
        assert isinstance(r, (decimal.Decimal, float, int))
        assert isinstance(t, (decimal.Decimal, float, int))
        return cls.from_lbrt(float(l), float(b), float(r), float(t))

    @classmethod
    def from_pdf_box_array_obj(cls, pdf_box_array_obj: pikepdf.Object) -> Rect:
        assert isinstance(pdf_box_array_obj, pikepdf.Array)
        return cls.from_pdf_box_array(pdf_box_array_obj)

    @classmethod
    def from_pdf_polygon_array(cls, pdf_cor_array: pikepdf.Array) -> Rect:
        if len(pdf_cor_array) == 10:
            blx, bly, brx, bry, trx, tr_y, tlx, tly, blx2, bly2 = pdf_cor_array
            assert blx2 == blx
            assert bly2 == bly
        else:
            blx, bly, brx, bry, trx, tr_y, tlx, tly = pdf_cor_array
        assert isinstance(blx, (decimal.Decimal, float, int))
        assert isinstance(bly, (decimal.Decimal, float, int))
        assert isinstance(brx, (decimal.Decimal, float, int))
        assert isinstance(bry, (decimal.Decimal, float, int))
        assert isinstance(trx, (decimal.Decimal, float, int))
        assert isinstance(tr_y, (decimal.Decimal, float, int))
        assert isinstance(tlx, (decimal.Decimal, float, int))
        assert isinstance(tly, (decimal.Decimal, float, int))
        assert blx == tlx
        assert brx == trx
        assert bly == bry
        assert tly == tr_y
        return cls.from_lbrt(float(blx), float(bly), float(trx), float(tr_y))

    @classmethod
    def from_pdf_polygon_array_obj(cls,
                                   pdf_cor_array_obj: pikepdf.Object) -> Rect:
        assert isinstance(pdf_cor_array_obj, pikepdf.Array)
        return cls.from_pdf_polygon_array(pdf_cor_array_obj)

    def __repr__(self) -> str:
        return 'Rect(%s, %s, %s, %s)' % (self._x, self._y, self._w, self._h)

    @property
    def x(self) -> float:
        return self._x

    @property
    def y(self) -> float:
        return self._y

    @property
    def w(self) -> float:
        return self._w

    @property
    def h(self) -> float:
        return self._h

    @property
    def l(self) -> float:
        return self._x

    @property
    def b(self) -> float:
        return self._y

    @property
    def r(self) -> float:
        return self._x + self._w

    @property
    def t(self) -> float:
        return self._y + self._h


def faa_tpp_iap_georef_chart_diy(pdf_path: str) -> ChartGeorefInfo | None:
    with pikepdf.Pdf.open(pdf_path) as pdf:
        page, = pdf.pages

        if '/VP' not in page:
            # Not georeferenced.
            return None

        viewport, = page['/VP']
        bbox = Rect.from_pdf_box_array_obj(viewport['/BBox'])
        measure = viewport['/Measure']
        gcs = measure['/GCS']
        assert gcs['/Type'] == '/PROJCS'
        wkt = gcs['/WKT']

        # This is very specific to the PDFs being consumed.
        #
        # TODO: improve WKT parsing.
        #
        # https://docs.ogc.org/is/18-010r7/18-010r7.html,
        # https://en.wikipedia.org/wiki/Well-known_text_representation_of_coordinate_reference_systems,
        # ISO 19162:2019.
        match = re.match(
            r'PROJCS\["[^"]*",'
            r'GEOGCS\["GCS_North_American_1983",'
            r'DATUM\["D_North_American_1983",'
            r'SPHEROID\["GRS_1980",([0-9.+-]+),([0-9.+-]+)\]\],'
            r'PRIMEM\["Greenwich",0\],'
            r'UNIT\["Degree",([0-9.+-]+)\]\],'
            r'PROJECTION\["Lambert_Conformal_Conic"\].'
            r'PARAMETER\["False_Easting",([0-9.+-]+)\],'
            r'PARAMETER\["False_Northing",([0-9.+-]+)\],'
            r'PARAMETER\["Central_Meridian",([0-9.+-]+)\],'
            r'PARAMETER\["Latitude_Of_Origin",([0-9.+-]+)\],'
            r'PARAMETER\["Standard_Parallel_1",([0-9.+-]+)\],'
            r'PARAMETER\["Standard_Parallel_2",([0-9.+-]+)\],'
            r'UNIT\["Inch",([0-9.+-]+)\]\]$', str(wkt))
        assert match is not None
        (
            ellipsoid_a,
            ellipsoid_inv_f,
            angle_unit,
            lambert_false_easting,
            lambert_false_northing,
            lambert_ori_lon,
            lambert_ori_lat,
            lambert_sp_lat_1,
            lambert_sp_lat_2,
            projection_unit,
        ) = (float(x) for x in match.groups())

        assert math.degrees(angle_unit) == 1.0
        assert lambert_false_easting == 0.0
        assert lambert_false_northing == 0.0

        # These inches are based on the US survey foot, 39.37 per meter. The PDF
        # only gives 13 digits beyond the decimal point.
        assert math.isclose(projection_unit, 1 / 39.37, rel_tol=1e-13)

        lambert = lambert_conformal_conic.LambertConformalConic(
            lambert_conformal_conic.Ellipsoid(ellipsoid_a,
                                              inv_f=ellipsoid_inv_f),
            lambert_conformal_conic.Angle(lambert_ori_lat, 'deg'),
            lambert_conformal_conic.Angle(lambert_ori_lon, 'deg'),
            lambert_conformal_conic.Angle(lambert_sp_lat_1, 'deg'),
            lambert_conformal_conic.Angle(lambert_sp_lat_2, 'deg'),
            lambert_false_easting,
            lambert_false_northing,
        )

        # TODO: Come up with better, more uniform naming for all of these
        # variables.

        bounds = Rect.from_pdf_polygon_array_obj(measure['/Bounds'])
        gpts = measure['/GPTS']
        assert isinstance(gpts, pikepdf.Array)
        bl_lat, bl_lon, br_lat, br_lon, tr_lat, tr_lon, tl_lat, tl_lon = (
            lambert_conformal_conic.Angle(float(x), 'deg') for x in gpts)

        bl_e, bl_n = lambert.forward(bl_lat, bl_lon)
        br_e, br_n = lambert.forward(br_lat, br_lon)
        tl_e, tl_n = lambert.forward(tl_lat, tl_lon)
        tr_e, tr_n = lambert.forward(tr_lat, tr_lon)

        # This is only valid if the difference between (bl_e, tl_e) and other
        # pairs is very small. Empirically, 1e-7 is acceptable.
        if not all((
                math.isclose(bl_e, tl_e, rel_tol=1e-7),
                math.isclose(br_e, tr_e, rel_tol=1e-7),
                math.isclose(bl_n, br_n, rel_tol=1e-7),
                math.isclose(tl_n, tr_n, rel_tol=1e-7),
        )):
            # TODO: Deal with this. Alternatively, get the underlying data
            # fixed, and raise an exception if it ever occurs again.
            #
            # 05018IL5.PDF (PPG/NSTU ILS or LOC 5) in cycle 2605 has a very
            # slightly rotated plan view. I had originally thought that this
            # would need to fall back to a least squares solution, but given
            # that the rotation is uniform (bl_e - tl_e == br_e - tr_e, bl_n
            # - br_n == tl_n - tr_n), it’s probably possible to just determine
            # the rotation and build a transform around that. Of course, a
            # least-squares solution would be most general. For least-squares,
            # see gdal gcore/gdal_misc.cpp GDALGCPsToGeoTransform as called by
            # frmts/pdf/pdfdataset.cpp PDFDataset::ParseMeasure.
            if not all((math.isclose(tl_e - bl_e, tr_e - br_e, rel_tol=1e-7),
                        math.isclose(br_n - bl_n, tr_n - tl_n, rel_tol=1e-7))):
                raise ValueError(
                    'not close',
                    bl_e - tl_e,
                    br_e - tr_e,
                    bl_n - br_n,
                    tl_n - tr_n,
                )

            rot0_rad = math.atan2(statistics.fmean((tl_e - bl_e, tr_e - br_e)),
                                  statistics.fmean((tl_n - bl_n, tr_n - br_n)))
            rot1_rad = math.atan2(statistics.fmean((br_n - bl_n, tr_n - tl_n)),
                                  statistics.fmean((tr_e - tl_e, br_e - bl_e)))
            assert math.isclose(rot0_rad, -rot1_rad, rel_tol=1e-7)
            rot_rad = statistics.fmean((rot0_rad, -rot1_rad))

            warnings.warn('PDF chart %s is rotated by %f°' %
                          (pdf_path, math.degrees(rot_rad)))

            return None

        # l_* correspond to the LPTS.
        l_e0 = statistics.fmean((bl_e, tl_e)) / projection_unit
        l_e1 = statistics.fmean((br_e, tr_e)) / projection_unit
        l_n0 = statistics.fmean((bl_n, br_n)) / projection_unit
        l_n1 = statistics.fmean((tl_n, tr_n)) / projection_unit
        l_efull = l_e1 - l_e0
        l_nfull = l_n1 - l_n0

        lpts = Rect.from_pdf_polygon_array_obj(measure['/LPTS'])

        # In practice, the LPTS cover a range of .1 to .9 inside the bbox. Now
        # work out the bbox.
        b_e0 = l_e0 - l_efull * ((lpts.l - bounds.l) / lpts.w)
        b_e1 = l_e1 + l_efull * ((bounds.r - lpts.r) / lpts.w)
        b_n0 = l_n0 - l_nfull * ((lpts.b - bounds.b) / lpts.h)
        b_n1 = l_n1 + l_nfull * ((bounds.t - lpts.t) / lpts.h)
        b_efull = b_e1 - b_e0
        b_nfull = b_n1 - b_n0

        # There’s also ArtBox, TrimBox, BleedBox, and CropBox, but GDAL seems to
        # only use the MediaBox. gdal frmts/pdf/pdfdataset.cpp
        # PDFDataset::ParseMeasure. Just match that.
        page_box = Rect.from_pdf_box_array_obj(page['/MediaBox'])

        # TODO: Unify the calculation of the “b” and “p” values, which follow
        # the same algorithm to “expand” into the space of an enclosing box.

        # In practice, the bbox is [9.18 2.628 378.18 591.372] relative to the
        # page box, [0 0 387.36 594]. Now work out the page. These correspond to
        # `gdal info`’s “Upper/Lower Left/Right” coordinates on the length
        # scale.
        p_e0 = b_e0 - b_efull * ((bbox.l - page_box.l) / bbox.w)
        p_e1 = b_e1 + b_efull * ((page_box.r - bbox.r) / bbox.w)
        p_n0 = b_n0 - b_nfull * ((bbox.b - page_box.b) / bbox.h)
        p_n1 = b_n1 + b_nfull * ((page_box.t - bbox.t) / bbox.h)
        p_efull = p_e1 - p_e0
        p_nfull = p_n1 - p_n0

        # The center of the image (`gdal info`’s “Center” coordinates).
        p_cx = p_e0 + p_efull / 2
        p_cy = p_n0 + p_nfull / 2

        # Determine the geographic coordinates corresponding to the page corners
        # and center. These correspond to `gdal info`’s “Upper/Lower Left/Right”
        # and “Center” coordinates in geographic terms. It also has a
        # translation of the origin, for good measure.
        lls: dict[str, tuple[lambert_conformal_conic.Angle,
                             lambert_conformal_conic.Angle]] = {}
        for cor_name, p_e, p_n in (
            ('tl', p_e0, p_n1),
            ('bl', p_e0, p_n0),
            ('tr', p_e1, p_n1),
            ('br', p_e1, p_n0),
            ('cc', p_cx, p_cy),
            ('or', 0, 0),
        ):
            lls[cor_name] = lambert.reverse(p_e * projection_unit,
                                            p_n * projection_unit)

        return ChartGeorefInfo(
            os.path.basename(pdf_path),
            {
                'proj': 'lcc',  # This was checked in wkt above.
                'datum': 'NAD83'  # This was checked in wkt above.
            },
            str(wkt),
            lambert_sp_lat_1,
            lambert_sp_lat_2,
            LatLon(lambert_ori_lat, lambert_ori_lon),
            {
                (0.0, 1.0): LatLon(*(angle.deg for angle in lls['tl'])),
                (0.0, 0.0): LatLon(*(angle.deg for angle in lls['bl'])),
                (1.0, 1.0): LatLon(*(angle.deg for angle in lls['tr'])),
                (1.0, 0.0): LatLon(*(angle.deg for angle in lls['br']))
            },
        )
