# Public report link

The shared read-only report is hosted at:

https://silvercatsc.github.io/A1_Monitoring/

This is deliberately public. It contains vehicle details (including VIN), public
advert links, observed prices/positions, coverage warnings and marketing findings.
It excludes operational controls, workbook links, raw evidence and local APIs.
It is a published snapshot, not a running monitoring service. On this Mac,
automatic publication is enabled by `artifacts/publication/settings.json`.
Start Monitoring publishes the exact completed or partial report-bearing cycle.
Continue publishes the resumed cycle or updated search supplement. Publication
does not start a scan or send marketplace traffic.

The scan date (UTC), completed search-filter count, unfinished filter names and
catalogue/identity gaps are shown at the top, on all screen sizes. An incomplete
check never becomes an inactive advert. A failed launch without a verified
report leaves the existing public snapshot available with its original date.

Publication uses the existing GitHub sign-in and ordinary, non-forced pushes.
The scan status is preserved independently of publication failures. Anonymous
readback distinguishes `published` from `submitted` (deployment still pending).
Status is recorded in `artifacts/publication/last_status.json` and, for
report-bearing runs, `artifacts/host_runs/<run>/publication_status.json`.

If publication fails, check GitHub sign-in/connectivity and use Continue again.
When no technical checks remain, Continue retries publication without scanning.
An older scan cannot replace a newer public report. To disable future updates,
set `enabled` to `false` in the local settings file.

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
