# FAA TPP IAP Georeferencing

This repository contains source code demonstrating a method to extract
georeferencing data from instrument approach procedure (IAP) charts published in
PDF format by the [FAA](https://www.faa.gov/) as part of their [Digital Terminal
Procedures Publication
(d-TPP)](https://www.faa.gov/air_traffic/flight_info/aeronav/digital_products/dtpp/)
product. This demonstration is made in support of the [Aeronautical Charting
Meeting—Charting
Group](https://www.faa.gov/air_traffic/flight_info/aeronav/acf/) recommendation
[CG 26-01-411: FAA IAP Georeferencing Data
Availability](https://www.faa.gov/air_traffic/flight_info/aeronav/acf/media/RDs/C_26-01-411_IAP_Georeferencing_Data.pdf).

## Documentation

Documentation is in the [doc](doc/) directory.

 - [IAP_Georeferencing.pdf](doc/IAP_Georeferencing.pdf) ([download
   link](https://raw.githubusercontent.com/markmentovai/faa_tpp_iap_georef/main/doc/IAP_Georeferencing.pdf))
   contains slides from a 2026-08-20 meeting, during which this work was
   presented.
 - [IAP_Georeferencing_PDF_Boxes.pdf](doc/IAP_Georeferencing_PDF_Boxes.pdf)
   ([download
   link](https://raw.githubusercontent.com/markmentovai/faa_tpp_iap_georef/main/doc/IAP_Georeferencing_PDF_Boxes.pdf))
   contains follow-up slides distributed on 2026-08-21. These slides demonstrate
   concepts relevant to questions about the selection of LPTS points in IAP
   chart PDFs, and how they relate to the enclosing viewport BBox and page
   dimensions (MediaBox and CropBox).

Documentation, including the presentation slides and this README.md, is licensed
under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

## Source Code

Source code for the example programs in IAP_Georeferencing.pdf is in the
[src](src/) directory.

### Executable Programs

 - [`faa_tpp_iap_georef`](src/faa_tpp_iap_georef.py) is the main program, but
   was not explictly featured during the presentation. It is capable of both CSV
   and XML (d-TPP_Metafile.xml) output (`--format`, XML by default), allows
   parallel operation for increased performance (`--parallel`, on by default),
   and can use the `faa_tpp_iap_georef_diy` module (`--strategy=diy`) which
   performs all geotransforms without external code dependencies, eliminating
   the reliance on [rasterio](https://rasterio.readthedocs.io/) and
   [GDAL](https://gdal.org/). In DIY mode, either
   [pikepdf](https://pikepdf.readthedocs.io/en/latest/) or
   [pypdf](https://pypdf.readthedocs.io/) can be used for PDF access
   (`--diy-pdf-module`).
 - [`faa_tpp_iap_georef_csv`](src/faa_tpp_iap_georef_csv.py), was featured
   during the presentation. It’s a scaled-back version of `faa_tpp_iap_georef`,
   and is limited to CSV output.
 - [`faa_tpp_iap_georef_xml`](src/faa_tpp_iap_georef_xml.py) is similar to
   `faa_tpp_iap_georef_csv`, but is scaled back to demonstrate only XML
   (d-TPP_Metafile.xml) output.

### Support Code

 - [`faa_tpp_iap_georef_diy`](src/faa_tpp_iap_georef_diy.py), described above as
   used by `faa_tpp_iap_georef`.
 - [`lambert_conformal_conic`](src/lambert_conformal_conic.py), which implements
   the forward and reverse Lambert Conformal Conic transformations according to
   [EPSG Guidance Note
   7-2](https://www.iogp.org/bookstore/product/coordinate-conversions-and-transformation-including-formulas/).
   This module is used by `faa_tpp_iap_georef_diy`.
 - [`faa_tpp_iap_georef_types`](src/faa_tpp_iap_georef_types.py), which breaks
   out common types shared by `faa_tpp_iap_georef` and `faa_tpp_iap_georef_diy`.

The source code in this repository is made available under the [Apache
license, version 2.0](https://www.apache.org/licenses/LICENSE-2.0).

## Example Output

The [CSV](output/IAP_Georeferencing.csv) and [d-TPP_Metafile.xml with
georeferencing](output/d-TPP_Metafile_Georeferenced.xml) example output of these
programs is included for inspection in the [output](output/) directory. These
files were produced by running the programs above with data from d-TPP cycle
2608, effective 2026-08-06.
