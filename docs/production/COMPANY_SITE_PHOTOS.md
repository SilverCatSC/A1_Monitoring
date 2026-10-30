# Company-site illustrations

The dashboard can use an A1auto.ru gallery image from the explicit link in column
Z of Размещение_автомобилей. This is an illustration of the marketing row, not
proof of an advert identity or of activity on Auto.ru/Avito.

`service.company_site_photos` reads the local `company_site_photos.json` index
under the evidence directory. Only marketing-row tiles with an unambiguous VIN
correspondence inside the workbook receive the image. An unbound marketplace
card never gains an image or a unique_id through VIN, price or model. Website
VIN text is an additional veto against stale links, not a marketplace join.

Images come only from the main vehicle gallery on an HTTPS A1auto.ru page;
logos, trackers, off-domain images and SVGs are excluded. The company hosts the
images; the dashboard loads them lazily with no referrer. Labels distinguish
website photos, feed photos and evidence screenshots. Details includes the
source page and capture date. No network fetch occurs during dashboard readback.

To refresh, first save a bounded read of the original sheet's row/status/VIN and
Z links in the documented marketing_links.json format, then run:

```sh
PYTHONPATH=src:. .venv312/bin/python scripts/update_company_site_photos.py \
  --links artifacts/company_photos_20261028/marketing_links.json \
  --output artifacts/evidence/company_site_photos.json
```

The collector checks three company pages concurrently, follows only same-site
vehicle redirects, preserves downloaded page HTML/hash and validates the first
gallery image response. It never contacts Auto.ru/Avito or changes the sheet.
Do not reuse an old workbook read as a claim that the current source was refreshed.

28 October verification: 19 dashboard illustrations added; six links left
unconfirmed (three missing marketing VIN, two different website VIN, one missing
website VIN). Ten current rows have no website link. 35 focused tests passed;
advert IDs, states, prices, years and the original saved report hash are unchanged.
Evidence: `artifacts/company_photos_20261028/`.
