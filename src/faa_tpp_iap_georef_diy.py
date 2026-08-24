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
import itertools
import math
import os
import re
import statistics
import typing
import warnings

if typing.TYPE_CHECKING:
    import pikepdf
    import pypdf
    import pypdf.generic
    _pdf_modules_l = ['pikepdf', 'pypdf']
else:
    _pdf_modules_l: list[str] = []
    try:
        import pikepdf
        _pdf_modules_l.append('pikepdf')
    except ImportError:
        import pypdf
        import pypdf.generic
        _pdf_modules_l.append('pypdf')
    else:
        try:
            import pypdf
            import pypdf.generic
            _pdf_modules_l.append('pypdf')
        except ImportError:
            pass
PDF_MODULES = tuple(_pdf_modules_l)
del _pdf_modules_l

import faa_tpp_iap_georef_types
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
    def from_pdf_box_array(
            cls,
            pdf_box_array: pikepdf.Array | pypdf.generic.ArrayObject) -> Rect:
        l, b, r, t = pdf_box_array
        assert isinstance(l, (decimal.Decimal, float, int))
        assert isinstance(b, (decimal.Decimal, float, int))
        assert isinstance(r, (decimal.Decimal, float, int))
        assert isinstance(t, (decimal.Decimal, float, int))
        return cls.from_lbrt(float(l), float(b), float(r), float(t))

    @classmethod
    def from_pdf_box_array_obj(
            cls, pdf_box_array_obj: pikepdf.Object | pypdf.generic.PdfObject
    ) -> Rect:
        assert isinstance(pdf_box_array_obj,
                          (pikepdf.Array, pypdf.generic.ArrayObject))
        return cls.from_pdf_box_array(pdf_box_array_obj)

    @classmethod
    def from_pdf_polygon_array(
            cls,
            pdf_cor_array: pikepdf.Array | pypdf.generic.ArrayObject) -> Rect:
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
    def from_pdf_polygon_array_obj(
            cls, pdf_cor_array_obj: pikepdf.Object | pypdf.generic.PdfObject
    ) -> Rect:
        assert isinstance(pdf_cor_array_obj,
                          (pikepdf.Array, pypdf.generic.ArrayObject))
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


def _transform_en_from_to_rect(
    en_rect: Rect,
    from_rect: Rect,
    to_rect: Rect,
) -> Rect:
    # Transforms the “en” (easting/northing) coordinates in `en_rect` that
    # correspond to x/y coordinates in `from_rect` to be the easting/northing
    # values corresponding the x/y coordinates in `to_rect`.
    #
    # This can be used to “expand” easting/northing coordinates from a smaller
    # rectangle within a page to a larger one, such as converting from LPTS to a
    # viewport BBox, or from a viewport BBox to the page’s MediaBox/CropBox.
    return Rect.from_lbrt(
        en_rect.l + en_rect.w * ((to_rect.l - from_rect.l) / from_rect.w),
        en_rect.b + en_rect.h * ((to_rect.b - from_rect.b) / from_rect.h),
        en_rect.r + en_rect.w * ((to_rect.r - from_rect.r) / from_rect.w),
        en_rect.t + en_rect.h * ((to_rect.t - from_rect.t) / from_rect.h))


def _georef_chart_page(
    pdf_path: os.PathLike[str] | str, page: pikepdf.Page | pypdf.PageObject
) -> faa_tpp_iap_georef_types.ChartGeorefInfo | None:
    if '/VP' not in page:
        # Not georeferenced.
        return None

    viewport_array = page['/VP']
    assert isinstance(viewport_array,
                      (pikepdf.Array, pypdf.generic.ArrayObject))
    viewport, = viewport_array
    measure = viewport['/Measure']
    gcs = measure['/GCS']
    assert gcs['/Type'] == '/PROJCS'
    wkt = gcs['/WKT']

    # This is very specific to the TPP PDFs being consumed.
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
        lambert_conformal_conic.Ellipsoid(ellipsoid_a, inv_f=ellipsoid_inv_f),
        lambert_conformal_conic.Angle(lambert_ori_lat, 'deg'),
        lambert_conformal_conic.Angle(lambert_ori_lon, 'deg'),
        lambert_conformal_conic.Angle(lambert_sp_lat_1, 'deg'),
        lambert_conformal_conic.Angle(lambert_sp_lat_2, 'deg'),
        lambert_false_easting,
        lambert_false_northing,
    )

    gpts = measure['/GPTS']
    assert isinstance(gpts, (pikepdf.Array, pypdf.generic.ArrayObject))
    gpt_ll_bl, gpt_ll_br, gpt_ll_tr, gpt_ll_tl = tuple(
        itertools.batched(
            (lambert_conformal_conic.Angle(float(x), 'deg') for x in gpts),
            2,
            strict=True))

    # “en” is easting/northing.
    gpt_en_bl_e, gpt_en_bl_n = lambert.forward(*gpt_ll_bl)
    gpt_en_br_e, gpt_en_br_n = lambert.forward(*gpt_ll_br)
    gpt_en_tr_e, gpt_en_tr_n = lambert.forward(*gpt_ll_tr)
    gpt_en_tl_e, gpt_en_tl_n = lambert.forward(*gpt_ll_tl)

    # This is only valid if the difference between (gpt_en_bl_e, gpt_en_tl_e)
    # and between other pairs is very small (effectively zero). The tolerance
    # values were chosen empirically.
    if not all((
            math.isclose(gpt_en_bl_e, gpt_en_tl_e, rel_tol=1e-7),
            math.isclose(gpt_en_br_e, gpt_en_tr_e, rel_tol=1e-7),
            math.isclose(gpt_en_bl_n, gpt_en_br_n, rel_tol=1e-7),
            math.isclose(gpt_en_tl_n, gpt_en_tr_n, rel_tol=1e-7),
    )):
        # If the differences are nonzero but they are uniform enough, the plan
        # view has been rotated away from true north being up. In cycle 2608,
        # this only occurs for 05018IL5.PDF (PPG/NSTU ILS or LOC 5). It is
        # possible to deal with this, but the chart is probably in error and
        # should be corrected, so just make it a warning for now.
        if all((math.isclose(gpt_en_tl_e - gpt_en_bl_e,
                             gpt_en_tr_e - gpt_en_br_e,
                             rel_tol=1e-7),
                math.isclose(gpt_en_br_n - gpt_en_bl_n,
                             gpt_en_tr_n - gpt_en_tl_n,
                             rel_tol=1e-7))):
            rot0_rad = math.atan2(
                statistics.fmean(
                    (gpt_en_tl_e - gpt_en_bl_e, gpt_en_tr_e - gpt_en_br_e)),
                statistics.fmean(
                    (gpt_en_tl_n - gpt_en_bl_n, gpt_en_tr_n - gpt_en_br_n)))
            rot1_rad = math.atan2(
                statistics.fmean(
                    (gpt_en_br_n - gpt_en_bl_n, gpt_en_tr_n - gpt_en_tl_n)),
                statistics.fmean(
                    (gpt_en_tr_e - gpt_en_tl_e, gpt_en_br_e - gpt_en_bl_e)))
            assert math.isclose(rot0_rad, -rot1_rad, rel_tol=1e-7)
            rot_rad = statistics.fmean((rot0_rad, -rot1_rad))

            warnings.warn('PDF chart %s is rotated by %f°' %
                          (pdf_path, math.degrees(rot_rad)))

            return None

        # Some other transformation has been applied, like a skew. That’s
        # unexpected! Don’t handle it.
        raise ValueError(
            'not close',
            gpt_en_bl_e - gpt_en_tl_e,
            gpt_en_br_e - gpt_en_tr_e,
            gpt_en_bl_n - gpt_en_br_n,
            gpt_en_tl_n - gpt_en_tr_n,
        )

    # Easting/northing for the GPTS coordinates, arranged as a rectangle.
    gpts_en = Rect.from_lbrt(statistics.fmean((gpt_en_bl_e, gpt_en_tl_e)),
                             statistics.fmean((gpt_en_bl_n, gpt_en_br_n)),
                             statistics.fmean((gpt_en_br_e, gpt_en_tr_e)),
                             statistics.fmean((gpt_en_tl_n, gpt_en_tr_n)))

    # LPTS are unit square coordinates (range 0–1) relative to the viewport
    # BBox. In practice, TPP PDFs always use [0.1 0.1 0.9 0.1 0.9 0.9 0.1 0.9],
    # covering .8 of the width and .8 of the height of the viewport BBox.
    lpts = Rect.from_pdf_polygon_array_obj(measure['/LPTS'])
    assert lpts.l <= lpts.r
    assert lpts.b <= lpts.t

    # “Expand” to get easting/northing at the corners of the viewport BBox.
    viewport_bbox_en = _transform_en_from_to_rect(
        gpts_en, lpts, Rect.from_lbrt(0.0, 0.0, 1.0, 1.0))

    # The viewport BBox is in page coordinates (1/72″). In practice, TPP PDFs
    # all have a viewport BBox of [9.18 2.628 378.18 591.372].
    viewport_bbox = Rect.from_pdf_box_array_obj(viewport['/BBox'])

    # The x-axis and y-axis scales should be identical. The tolerance value was
    # chosen empirically.
    #
    # This comparison is done in ellipsoid units (specified in meters in
    # practice) per PDF page coordinate unit (1/72″), which isn’t terribly
    # helpful in itself, but the calculation would be the same if it was
    # converted to something more useful like the cartographic scale.
    if not math.isclose(viewport_bbox_en.h / viewport_bbox.h,
                        viewport_bbox_en.w / viewport_bbox.w,
                        rel_tol=1e-9):
        raise ValueError(
            'unequal scale',
            viewport_bbox_en.h / viewport_bbox.h,
            viewport_bbox_en.w / viewport_bbox.w,
        )

    # There are also ArtBox, TrimBox, BleedBox, and CropBox. Of these, there are
    # arguments for using either MediaBox or CropBox. GDAL seems to only
    # MediaBox (gdal frmts/pdf/pdfdataset.cpp PDFDataset::ParseMeasure). Just
    # match that. Thse are in page coordinates (1/72″). In practice, in TPP
    # PDFs, both MediaBox and CropBox are supplied and are identical: [0 0
    # 387.36 594], for page dimensions of 5.38″×8.25″.
    page_box = Rect.from_pdf_box_array_obj(page['/MediaBox'])

    # “Expand” to get easting/northing at the page corners.
    page_en = _transform_en_from_to_rect(viewport_bbox_en, viewport_bbox,
                                         page_box)

    # Determine the geographic coordinates corresponding to the page corners and
    # center. These correspond to `gdal info`’s “Upper/Lower Left/Right”
    # coordinates in geographic terms.
    page_ll = dict((xy,
                    faa_tpp_iap_georef_types.LatLon(
                        *(angle.deg
                          for angle in lambert.reverse(*page_en))))
                   for xy, page_en in (
                       ((0.0, 0.0), (page_en.l, page_en.b)),
                       ((1.0, 0.0), (page_en.r, page_en.b)),
                       ((1.0, 1.0), (page_en.r, page_en.t)),
                       ((0.0, 1.0), (page_en.l, page_en.t)),
                   ))

    return faa_tpp_iap_georef_types.ChartGeorefInfo(
        os.path.basename(pdf_path),
        {
            'proj': 'lcc',  # This was checked in wkt above.
            'datum': 'NAD83'  # This was checked in wkt above.
        },
        str(wkt),
        lambert_sp_lat_1,
        lambert_sp_lat_2,
        faa_tpp_iap_georef_types.LatLon(lambert_ori_lat, lambert_ori_lon),
        page_ll,
    )


def faa_tpp_iap_georef_chart_diy_pikepdf(
    pdf_path: os.PathLike[str] | str
) -> faa_tpp_iap_georef_types.ChartGeorefInfo | None:
    with pikepdf.Pdf.open(os.fspath(pdf_path)) as pdf:
        page, = pdf.pages
        return _georef_chart_page(pdf_path, page)


def faa_tpp_iap_georef_chart_diy_pypdf(
    pdf_path: os.PathLike[str] | str
) -> faa_tpp_iap_georef_types.ChartGeorefInfo | None:
    with pypdf.PdfReader(os.fspath(pdf_path)) as pdf:
        if pdf.get_num_pages() != 1:
            raise ValueError(pdf.get_num_pages())
        page = pdf.get_page(0)

        return _georef_chart_page(pdf_path, page)


def faa_tpp_iap_georef_chart_diy(
    pdf_path: os.PathLike[str] | str,
    *,
    pdf_module: str | None = None
) -> faa_tpp_iap_georef_types.ChartGeorefInfo | None:
    if pdf_module is None:
        pdf_module = PDF_MODULES[0]

    if pdf_module == 'pikepdf':
        return faa_tpp_iap_georef_chart_diy_pikepdf(pdf_path)

    assert pdf_module == 'pypdf'
    return faa_tpp_iap_georef_chart_diy_pypdf(pdf_path)
