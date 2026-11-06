# `make deploy` - the app on Cloud Run, behind Google sign-in

*Read this before deploying, letting a tester in, or touching the cloud setup.*

The app runs as one Cloud Run service, `honest-healthcare` in `us-east1`, in the Google Cloud project `honest-healthcare`.
Google's Identity-Aware Proxy (IAP) stands in front of it, its `run.app` address included, so nobody reaches it without signing in with a Google account the project lets in.
The image holds the code and the find-care tables ([README.md](README.md)), so a deploy ships both and a rollback restores both.

## Deploy

`make deploy` ships the committed code with the tables in `data/serving/` (`infra/deploy.sh`):

1. It builds the image on this machine for Cloud Run (`linux/amd64`), tagged with the commit and when the tables were built.
2. It pushes the image to the Artifact Registry repository `honest-healthcare`, which keeps the last three (`infra/cleanup-policy.json`).
3. It rolls it out at one instance at most, scaling to nothing when idle, with IAP on.
4. It runs `infra/smoke.py` through IAP, signed in as the `deploy-smoke` service account.

The first run also does whatever setup is missing: the APIs, the repository and the `deploy-smoke` account, which is in IAP's policy and whose tokens the person deploying may make.
A data refresh is `make refresh`, then `make deploy`.

## Who can sign in

A tester needs a Google account, on two lists:

- **IAP's policy:** Cloud Run → `honest-healthcare` → Security → IAP → Edit policy, one principal per tester.
  From the command line it's `gcloud iap web add-iam-policy-binding --project honest-healthcare --region us-east1 --resource-type cloud-run --service honest-healthcare --member user:<email> --role roles/iap.httpsResourceAccessor`, and `remove-iam-policy-binding` with the same flags shuts them out.
- **The sign-in screen's test users:** Google Auth Platform → Audience → Test users.
  The sign-in screen is in Testing, which admits only the accounts listed there, up to 100 over its lifetime.
  Publishing it ends that list, but needs a home page and a privacy policy on a domain the project owns.

## Cost

- One instance at most bounds what the service can spend in a month, and it costs nothing while idle.
- A budget on the project's Cloud Run carries a **spend cap** of $10 a month: Google pauses the service when its estimated spend reaches it, until the next month or until the cap is lifted under Billing → Budgets & alerts.
  The cap reacts quickly to estimated costs, but not instantly.
- The rest stays in free tiers: the image is built here rather than in Cloud Build, Artifact Registry keeps three images, and IAP is free.

## Roll back

`gcloud run revisions list --project honest-healthcare --region us-east1` lists the revisions, each with the commit it runs (its `commit` label).
`gcloud run services update-traffic honest-healthcare --project honest-healthcare --region us-east1 --to-revisions <revision>=100` sends all traffic to one, and the next `make deploy` moves it to the new revision.

## One-time setup

The project has no organization, so these steps are console-only:

1. Cloud Run → `honest-healthcare` → Security: Require authentication, with IAP.
2. Google Auth Platform: Branding (app name, support and developer emails), and Audience External, in Testing, with the testers as test users.
3. Google Auth Platform → Clients: a Web application client whose redirect URI is `https://iap.googleapis.com/v1/oauth/clientIds/<its client ID>:handleRedirect`.
   Download its JSON (Add secret makes a new one once the creation dialog has closed), then run `bash infra/iap-client.sh <that file>`.
   Until IAP has the client, the service answers everyone with a 502 and nobody can sign in.
4. Billing → Budgets & alerts: the Cloud Run budget and its spend cap, above.
