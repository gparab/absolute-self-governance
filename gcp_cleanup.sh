#!/bin/bash
# GCP cleanup for gen-lang-client-0635705662 — remaining items only.
# Already done elsewhere: secrets migration, key rotations (OpenAI/Gemini/Firebase),
# empty Gemini project deletion. Run: bash gcp_cleanup.sh
set -euo pipefail
P=gen-lang-client-0635705662

echo "== 1. Trim old AI Studio build versions (keeps version-5, the live source) =="
# NOTE: do NOT delete the whole bucket — AI Studio redeploys need version-5.
for v in version-1 version-2 version-3 version-4; do
  gcloud storage rm -r "gs://ai-studio-bucket-759334106009-us-west2/services/goa-blog/$v" 2>/dev/null || echo "no $v"
done

echo "== 2. Delete stale run-sources zips + 30-day lifecycle rule =="
gcloud storage rm "gs://run-sources-$P-us-east1/services/goa-blog/*.zip"
echo '{"rule":[{"action":{"type":"Delete"},"condition":{"age":30}}]}' > /tmp/lifecycle.json
gcloud storage buckets update "gs://run-sources-$P-us-east1" --lifecycle-file=/tmp/lifecycle.json

echo "== 3. Delete 3 oldest goa-blog images (keeps newest, sha 5f1029df) =="
for d in \
  ce8b4d461e1b2915906c4e7c5e38f855418341de694a9315d40973839c5be446 \
  b6e4119945e14a782e9c6ea96c3d00eda425e72cb6bb931cb9ceca2cad65e91c \
  a0fdd096156f4a802e0ae5781bbc8649c136b07557997782202614b58d2b87b6; do
  gcloud artifacts docker images delete \
    "us-east1-docker.pkg.dev/$P/cloud-run-source-deploy/goa-blog@sha256:$d" \
    --delete-tags --quiet
done

echo "== 4. Delete retired Cloud Run revisions (00008 stays live) =="
# 00001-00005 embed the old plaintext secrets in their configs — delete these especially.
for r in goa-blog-00001-ccv goa-blog-00002-pxt goa-blog-00003-l6z \
         goa-blog-00004-sk9 goa-blog-00005-2kg goa-blog-00006-dxx goa-blog-00007-rm2; do
  gcloud run revisions delete "$r" --region us-west2 --project "$P" --quiet || echo "skip $r"
done

echo "== 5. Disable clearly-unused APIs (conservative set) =="
# Skips Firebase/Run/Storage/Secret Manager/API Keys — all in use by goa-blog.
for api in appengine.googleapis.com bigquerydatatransfer.googleapis.com \
  bigquerymigration.googleapis.com bigqueryreservation.googleapis.com \
  bigqueryconnection.googleapis.com bigquerydatapolicy.googleapis.com \
  analyticshub.googleapis.com dataform.googleapis.com dataplex.googleapis.com \
  testing.googleapis.com oslogin.googleapis.com sql-component.googleapis.com; do
  gcloud services disable "$api" --project "$P" --force || echo "skip $api"
done

echo "Done."
