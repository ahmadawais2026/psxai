# watch_logs.ps1 — live-tail the PSX Advisor backend logs (Cloud Run service "api", project aiforpsx).
# Usage:
#   ./watch_logs.ps1            # live tail (Ctrl+C to stop)
#   ./watch_logs.ps1 -Recent    # dump the last 1h instead of tailing
#
# Requires: gcloud + beta component (already installed). App must log at INFO
# (configured in app.py) for the full analysis pipeline to appear.

param([switch]$Recent)

$gc = "C:\Users\Awais Ahmed\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
$project = "aiforpsx"
$service = "api"
$region  = "us-central1"

if ($Recent) {
    Write-Host "== Last 1h of '$service' logs (project $project) ==" -ForegroundColor Cyan
    & $gc logging read "resource.type=`"cloud_run_revision`" AND resource.labels.service_name=`"$service`"" `
        --project $project --freshness=1h --limit 200 --order=asc `
        --format="value(timestamp, severity, textPayload)"
} else {
    Write-Host "== Live-tailing '$service' logs (project $project). Ctrl+C to stop. ==" -ForegroundColor Cyan
    Write-Host "Trigger an analysis on the site to watch the pipeline run." -ForegroundColor DarkGray
    & $gc beta run services logs tail $service --project $project --region $region
}
