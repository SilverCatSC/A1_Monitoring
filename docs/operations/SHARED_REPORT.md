# Public report link

The shared read-only report is hosted at:

https://silvercatsc.github.io/A1_Monitoring/

This is deliberately public. It contains vehicle details (including VIN), public
advert links, observed prices/positions, coverage warnings and marketing findings.
It excludes operational controls, workbook links, raw evidence and local APIs.
It is a published snapshot, not a running monitoring service. A new local scan
does **not** automatically replace the public report.

## Prepare an update

Use a new output directory each time:

```bash
cd /Users/filaret/Desktop/Monitoring
.venv312/bin/python scripts/export_shared_dashboard.py \
  --out artifacts/shared_report_NEW_TIMESTAMP \
  --public-vehicle-details-approved
```

This reads the latest report from the local app without starting a scan, fetching
workbooks or changing VPN. Review the snapshot before publishing. The flag is an
explicit acknowledgement that the exported vehicle details will be public.
This exporter is separate from the original aggregate-only public exporter.

## Publication

GitHub Pages serves the repository's `gh-pages` branch at `/`. That branch holds
only the generated site, bundled styles/fonts and a checksum manifest. Publish a
reviewed export to that branch with a normal, non-forced Git commit/push. Preserve
the branch history so earlier published snapshots can be recovered. Do not copy
the application, databases, `.env`, browser profiles or evidence directories into
the publication branch. No marketing chat message is sent by export or deployment.

Photos use previously selected public A1auto/feed image URLs. Their availability
can change independently of the published snapshot. Missing images do not alter
identity, activity or coverage findings.
