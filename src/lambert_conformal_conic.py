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

import math

# EPSG Guidance Note 7-2
# https://epsg.org/guidance-notes.html
# https://www.iogp.org/bookstore/product/coordinate-conversions-and-transformation-including-formulas/
# 3.4.1.1, Lambert Conic Conformal (2SP)
#
# I am referencing version 74, 2026-07, where this appears on page 23.
#
# Alternatively, the same method can be extracted from the EPSG database,
# https://epsg.io/9802-method.
#
# A further alternative: “Map Projections—A Working Manual”
# https://pubs.usgs.gov/pp/1395/report.pdf (1987), chapter 15, “Lambert
# Conformal Conic Projection”, page 116 (labeled 104).


class Ellipsoid:
    # EPSG Guidance Note 7-2, 1.1, Ellipsoid Parameters.

    __slots__ = ('_a', '_b', '_inv_f', '_f', '_e', '_e2')

    def __init__(self,
                 a: float,
                 *,
                 b: float | None = None,
                 inv_f: float | None = None):
        self._a = a  # a
        if not ((b is not None) ^ (inv_f is not None)):
            raise ValueError('Must provide exactly one of b and inv_f')
        if b is not None:
            self._b = b  # b
            self._inv_f = a / (a - b)  # 1/f
            self._f = 1 / self._inv_f  # f
        else:
            assert inv_f is not None
            self._inv_f = inv_f  # 1/f
            self._f = 1 / inv_f  # f
            self._b = a - a * self._f  # b

        self._e2 = 2 * self._f - self._f**2  # e²
        self._e = math.sqrt(self._e2)  # e

    @property
    def a(self) -> float:
        return self._a

    @property
    def b(self) -> float:
        return self._b

    @property
    def inv_f(self) -> float:
        return self._inv_f

    @property
    def f(self) -> float:
        return self._f

    @property
    def e(self) -> float:
        return self._e

    @property
    def e2(self) -> float:
        return self._e2


class Angle:
    __slots__ = ('_rad',)

    def __init__(self, value: float, unit: str):
        if unit == 'rad':
            self._rad = value
        elif unit == 'deg':
            self._rad = math.radians(value)
        else:
            raise ValueError(unit)

    @property
    def rad(self):
        return self._rad

    @property
    def deg(self):
        return math.degrees(self._rad)


class LambertConformalConic:
    # EPSG Guidance Note 7-2, 3.4.1.1, Lambert Conic Conformal (2SP).

    __slots__ = (
        '_ellipsoid',
        '_fo_lat',
        '_fo_lon',
        '_sp_lat_1',
        '_sp_lat_2',
        '_false_easting',
        '_false_northing',
        '_n',
        '_f',
        '_rf',
    )

    def __init__(
        self,
        ellipsoid: Ellipsoid,
        fo_lat: Angle,
        fo_lon: Angle,
        sp_lat_1: Angle,
        sp_lat_2: Angle,
        false_easting: float,
        false_northing: float,
    ):
        self._ellipsoid = ellipsoid
        self._fo_lat = fo_lat  # φꜰ
        self._fo_lon = fo_lon  # λꜰ
        self._sp_lat_1 = sp_lat_1  # φ₁
        self._sp_lat_2 = sp_lat_2  # φ₂
        self._false_easting = false_easting  # Eꜰ
        self._false_northing = false_northing  # Nꜰ

        m1 = self._calc_m(sp_lat_1.rad)  # m₁
        m2 = self._calc_m(sp_lat_2.rad)  # m₂
        t1 = self._calc_t(sp_lat_1.rad)  # t₁
        t2 = self._calc_t(sp_lat_2.rad)  # t₂
        self._n = (
            (math.log(m1) - math.log(m2)) / (math.log(t1) - math.log(t2)))  # n
        self._f = m1 / (self._n * t1**self._n)  # F
        self._rf = self._calc_r(fo_lat.rad)  # rꜰ

    @property
    def ellipsoid(self) -> Ellipsoid:
        return self._ellipsoid

    @property
    def fo_lat(self) -> Angle:
        return self._fo_lat

    @property
    def fo_lon(self) -> Angle:
        return self._fo_lon

    @property
    def sp_lat_1(self) -> Angle:
        return self._sp_lat_1

    @property
    def sp_lat_2(self) -> Angle:
        return self._sp_lat_2

    @property
    def false_easting(self) -> float:
        return self._false_easting

    @property
    def false_northing(self) -> float:
        return self._false_northing

    @property
    def n(self) -> float:
        return self._n

    @property
    def f(self) -> float:
        return self._f

    @property
    def rf(self) -> float:
        return self._rf

    def _calc_m(self, phi_rad: float) -> float:
        return (math.cos(phi_rad) /
                math.sqrt(1 - self._ellipsoid.e2 * math.sin(phi_rad)**2))

    def _calc_t(self, phi_rad: float) -> float:
        return (math.tan(math.pi / 4 - phi_rad / 2) /
                ((1 - self._ellipsoid.e * math.sin(phi_rad)) /
                 (1 + self._ellipsoid.e * math.sin(phi_rad)))
                **(self._ellipsoid.e / 2))

    def _calc_r(self, phi_rad: float) -> float:
        t = self._calc_t(phi_rad)
        return self._ellipsoid.a * self._f * t**self._n

    def forward(
            self,
            lat: Angle,  # φ
            lon: Angle  # λ
    ) -> tuple[float, float]:
        r = self._calc_r(lat.rad)  # r
        theta_rad = self._n * (lon.rad - self._fo_lon.rad)  # ϴ
        easting = self._false_easting + r * math.sin(theta_rad)  # E
        northing = (self._false_northing + self._rf - r * math.cos(theta_rad)
                   )  # N
        return easting, northing

    def reverse(
            self,
            easting: float,  # E
            northing: float  # N
    ) -> tuple[Angle, Angle]:
        r_prime = (math.copysign(
            math.sqrt((easting - self._false_easting)**2 +
                      (self._rf - (northing - self._false_northing))**2),
            self._n))  # r′
        t_prime = (r_prime / (self._ellipsoid.a * self._f))**(1 / self._n)  # t′
        theta_prime = math.atan2(
            (easting - self._false_easting) * math.copysign(1, self._n),
            (self._rf - (northing - self._false_northing)) *
            math.copysign(1, self._n))  # ϴ′

        lat_rad = math.pi / 2 - 2 * math.atan(t_prime)  # φ
        last_lat_rad = math.inf
        while lat_rad != last_lat_rad:
            last_lat_rad = lat_rad
            lat_rad = math.pi / 2 - 2 * math.atan(t_prime * (
                (1 - self._ellipsoid.e * math.sin(lat_rad)) /
                (1 + self._ellipsoid.e * math.sin(lat_rad)))**
                                                  (self._ellipsoid.e / 2))

        lon_rad = theta_prime / self._n + self._fo_lon.rad  # λ

        return Angle(lat_rad, 'rad'), Angle(lon_rad, 'rad')
