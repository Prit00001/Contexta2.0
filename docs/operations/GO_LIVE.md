# Go live: workspaces, sign-up and Razorpay billing on Render

Takes the Render service from "paper search only" to the full product: accounts, team workspaces,
document upload, and paid subscriptions after the free hour. About 45 minutes, in this order.
Paper search keeps working at every step; each block only switches on when its settings are complete.

Never paste keys into chat, commits or tickets. They go only into the Render dashboard
(Environment) and, for local testing, your own `.env`.

## 1. Supabase (database, sign-in, file storage)

1. [supabase.com](https://supabase.com) → **New project**. Region: **Mumbai (ap-south-1)** for Indian
   customers. Save the database password in your password manager.
2. **Connect** (top bar) → **Session pooler** → copy the URI. Replace `[YOUR-PASSWORD]`.
   This is `DATABASE_URL`. Use the *session pooler*, not "Direct connection": Render has no IPv6,
   and Supabase's direct host is IPv6-only.
3. **Project Settings → API Keys** → copy the **anon** (or *publishable*) key = `SUPABASE_ANON_KEY`,
   and the **service_role** key = `SUPABASE_SERVICE_KEY`. If the page shows the new
   `sb_publishable_…` / `sb_secret_…` keys, open the **Legacy API keys** tab and use the anon and
   service_role keys there. The service key bypasses all security rules: it must stay server-side.
4. **Project Settings → Data API** (or API) → copy the **Project URL** = `SUPABASE_URL`
   (`https://<ref>.supabase.co`).
5. **Project Settings → JWT Keys**: if a **Legacy JWT secret** is shown, copy it =
   `SUPABASE_JWT_SECRET`. Projects that use the new JWT signing keys need nothing here: the app
   checks those tokens against the project's public keys (`SUPABASE_URL` is enough).
6. **Storage → New bucket** → name `documents`, **private** (public off).
7. **Authentication → URL Configuration**:
   - Site URL: `https://<your-service>.onrender.com`
   - Redirect URLs: add `https://<your-service>.onrender.com/workspace`
   Sign-up confirmation and password-reset emails link back here.
8. **Authentication → Emails → SMTP Settings**: Supabase's built-in mailer sends only a few emails
   per hour and is meant for testing. Before inviting real customers, connect your own SMTP
   (Resend, Amazon SES, Zoho, …). Keep **Confirm email** on.

The database tables are created automatically on the next deploy (`MIGRATE_ON_START=true` in
`render.yaml` runs `scripts/migrate.py` before the server starts).

## 2. Razorpay (test mode first)

Work in **Test Mode** (toggle at the top of the dashboard) until a test payment succeeds end to end.

1. **Account & Settings → API Keys → Generate Test Key**. Key Id = `RAZORPAY_KEY_ID`
   (`rzp_test_…`), Key Secret = `RAZORPAY_KEY_SECRET` (shown once).
2. **Subscriptions → Plans → Create Plan**: billing frequency **monthly**, amount = **price of one
   seat** (for example ₹499). Plan Id = `RAZORPAY_PLAN_ID` (`plan_…`). The app subscribes with
   quantity = the workspace's member count.
3. **Account & Settings → Webhooks → Add New Webhook**:
   - URL: `https://<your-service>.onrender.com/billing/razorpay/webhook`
   - Secret: a long random string you make up (e.g. from your password manager) =
     `RAZORPAY_WEBHOOK_SECRET`
   - Active events: every `subscription.*` event: authenticated, activated, charged, pending,
     halted, cancelled, completed, updated, paused, resumed.
4. Pick the text shown next to the Subscribe button, e.g. `₹499 per seat / month` =
   `BILLING_PRICE_LABEL`. It must match the plan's amount; the plan decides what is charged.

## 3. Render

1. Merge the release PR into `main`. If the service was created from the Blueprint, Render picks up
   the new variables from `render.yaml`; otherwise add them by hand.
2. **Service → Environment**, add (values from steps 1 and 2):

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | Supabase session-pooler URI |
   | `SUPABASE_URL` | `https://<ref>.supabase.co` |
   | `SUPABASE_ANON_KEY` | anon key |
   | `SUPABASE_SERVICE_KEY` | service_role key |
   | `SUPABASE_JWT_SECRET` | legacy JWT secret, if your project shows one |
   | `STORAGE_BACKEND` | `supabase` |
   | `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | test key id / secret |
   | `RAZORPAY_WEBHOOK_SECRET` | the webhook secret you chose |
   | `RAZORPAY_PLAN_ID` | `plan_…` |
   | `BILLING_PRICE_LABEL` | e.g. `₹499 per seat / month` |
   | `BILLING_ENABLED` | `true` |

   `MIGRATE_ON_START=true` and `INGEST_IN_API=true` come from `render.yaml` (the free plan has no
   background workers, so the API process also ingests uploads).
3. **Save, rebuild and deploy**. In the deploy log look for `Applying 0001_init.sql` … `0004_billing.sql`
   and then `db=connected + ingest worker`.

## 4. Check it, in this order

1. `https://<service>/health` → `ok`.
2. `/workspace` → **Create an account** → confirmation email → link opens the app signed in →
   create a workspace. The banner shows 60 minutes left.
3. Upload a document → it turns **ready** within a minute → ask a question → cited answer.
4. **Forgot password?** → email → link → choose a new password.
5. **Billing → Subscribe** → Razorpay Checkout opens → pay with a
   [Razorpay test card](https://razorpay.com/docs/payments/payments/test-card-details/)
   → the page shows **Subscribed**. In Razorpay → **Webhooks**, the deliveries show `200`.
6. Members → add a second signed-up user → Billing shows 2 members (seats change at renewal).
7. Billing → **Cancel subscription** → Razorpay shows it cancelling at the end of the period.

When all of that passes: switch Razorpay to **Live Mode**, generate live keys, create the same plan
and webhook in live mode, replace the four `RAZORPAY_*` values on Render, and redeploy. Razorpay
must have activated your account for live payments (KYC) first.

## If something fails

| Symptom | Cause |
|---|---|
| Deploy log stops at `Applying …` with an error | `DATABASE_URL` wrong (password, or the direct host instead of the session pooler) |
| `/workspace` says workspaces are not enabled | `DATABASE_URL` missing or unreachable; the log says `db=UNREACHABLE` |
| Sign-in page asks for an "access token" | `SUPABASE_URL` or `SUPABASE_ANON_KEY` missing |
| Signed in, then every page says *Invalid token* | Legacy project: `SUPABASE_JWT_SECRET` missing or wrong |
| Confirmation link opens the Supabase site, or "redirect not allowed" | Step 1.7: Site URL / Redirect URLs |
| No confirmation email | Built-in mailer limit; set up SMTP (step 1.8) |
| Upload stays **queued** | `INGEST_IN_API` not `true`, or the service restarted mid-ingest (it resumes) |
| Upload fails with a storage error | Bucket `documents` missing, or `SUPABASE_SERVICE_KEY` wrong |
| Subscribe says *The payment provider refused the request* | Razorpay key id/secret or plan id wrong, or test/live mixed |
| Paid, but the workspace stays paywalled | Webhook URL or secret wrong: Razorpay → Webhooks shows the failed deliveries |
