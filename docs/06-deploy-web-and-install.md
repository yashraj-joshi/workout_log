# 06 - Deploy the web app and install it

Upload the app to the stack's S3 bucket, open it through CloudFront, sign in,
and add it to your iPhone's home screen.

**Time:** about 10 minutes.

You need doc 04 done (the stack is deployed) and doc 05 section 1 done (you
have an invite email with a temporary password).

---

## 1. Deploy

From the repo root, with everything committed:

```bash
make deploy-web
```

What it does, in order:

1. Refuses if `web/` has uncommitted changes, for the same reason as
   `make deploy`: what's live should be a commit you can look up.
2. `make check-secrets test-js`: the secret scan, which also fails if anything
   in `web/` mentions OpenAI, and every frontend test.
3. Copies `web/` to a temporary folder, without `tests/` and `config.example.js`.
4. Writes `config.js` into that copy from the stack outputs: the region, the
   user pool ID and the app client ID. None of these are secrets; every
   visitor's browser needs them to reach the sign-in service.
5. Stamps `sw.js` with the commit, so this deploy gets its own
   service-worker cache (section 6).
6. `aws s3 sync` to the bucket, deleting files you've removed:

   | Files | `Cache-Control` | Why |
   | --- | --- | --- |
   | `fonts/`, `vendor/` | `public, max-age=31536000, immutable` | Their names carry a version, so a changed file gets a new name |
   | everything else | `no-cache` | The browser checks with CloudFront each time and gets a small 304 when nothing changed |

7. A CloudFront invalidation of `/*`, so the edge stops serving the old copy.

Expected ending:

```
Uploading to s3://workout-log-webbucket-abc123 ...
Clearing CloudFront's cache ...

Deployed web 3f2a1bc to https://d1234abcd.cloudfront.net
CloudFront invalidation I2J3K4L5M6 takes a minute or two to finish.
Next: open https://d1234abcd.cloudfront.net and sign in (docs/06).
```

**Cost:** about 30 files, a few hundred KB in total, so S3 storage rounds to
$0.00. CloudFront gives 1,000 invalidation paths a month free, and `/*` counts
as one. A deploy costs well under $0.01.

**Check it worked:**

```bash
APP=$(aws cloudformation describe-stacks --stack-name workout-log \
  --query "Stacks[0].Outputs[?OutputKey=='AppUrl'].OutputValue" --output text)
curl -sI "$APP/" | grep -iE '^(HTTP|content-type|content-security-policy|cache-control)'
```

Expected:

```
HTTP/2 200
content-type: text/html
cache-control: no-cache
content-security-policy: default-src 'self'; script-src 'self'; ...
```

## 2. Find the app's address

It's the stack output `AppUrl`, which `make deploy-web` also prints:

```bash
aws cloudformation describe-stacks --stack-name workout-log \
  --query "Stacks[0].Outputs[?OutputKey=='AppUrl'].OutputValue" --output text
```

Expected: `https://d1234abcd.cloudfront.net`. It doesn't change unless you
delete the stack.

## 3. Sign in the first time, on a laptop

1. Open the `AppUrl` in a browser.
2. Enter your email and the temporary password from the invite email.
3. The app asks you to choose a password: at least 12 characters, with an
   upper-case letter, a lower-case letter and a number.
4. You land on the Day tab. The status line above the title says
   **Live · updates as you log**, and the card shows how many days are in your
   log (0 for a new account).

**Check it stays signed in:** close the tab, open the URL again. It should go
straight to the Day tab without asking for your password. That's the refresh
cookie at work.

## 4. Install it on your iPhone

The home-screen app keeps its own cookies, separate from Safari, so sign in
**inside the installed app**, not in a Safari tab.

1. Open the `AppUrl` in **Safari**. (Other iPhone browsers can add to the
   home screen too, but Safari is the path Apple documents.)
2. Tap **Share** (the square with the arrow) -> **Add to Home Screen**. If
   there's an **Open as Web App** switch, leave it on. Tap **Add**.
3. Open **Workout Log** from the home screen. It opens full screen, with no
   Safari address bar.
4. Sign in.

**Check it worked:**

- Swipe the app away in the app switcher, then open it again from the home
  screen. It should open signed in. If it asks for your password every time,
  see "Common errors".
- Turn on Airplane Mode and open the app. It should open and show your saved
  log with **Offline · showing saved data**. Turn Airplane Mode off; within a
  minute (or when you switch back to the app) it says **Live** again.

## 5. Android and desktop

- **Chrome on Android:** open the `AppUrl` -> menu (three dots) ->
  **Add to Home screen** or **Install app**.
- **Chrome or Edge on a laptop:** the install icon at the right end of the
  address bar, or menu -> **Cast, save and share** -> **Install page as app**.
- **Safari on a Mac:** **File** -> **Add to Dock**.

Each installed copy is a separate device for sign-in: you sign in once in
each.

## 6. How updates reach an installed app

The service worker (`web/sw.js`) keeps a copy of the app's files so it opens
instantly, even offline. Each `make deploy-web` stamps a new version into it.

- When the app opens, or comes back to the front, the phone asks CloudFront
  whether `sw.js` changed. It's `no-cache`, so the answer is current.
- If it changed, the new version downloads every file into a new cache, takes
  over, deletes the old cache, and the app reloads once. You'll see a quick
  flash a second or two after opening.
- Your data is never in that cache. The service worker doesn't touch `/v1/*`;
  the saved copy of your log is in the app's local storage, per user, and is
  wiped when you sign out.

To force it on an iPhone: close the app in the app switcher and open it again.

## 7. Signing out

**Account** (top right) shows the signed-in email and whether voice and
summaries are on for you.

- **Sign out** ends this device's session and deletes the saved log on it.
- **Sign out everywhere** also ends every other device's session. Use it if a
  phone is lost: the next time that phone reaches the server, it's signed out
  and its saved copy of the log is deleted.

Both need a connection. Offline, the app says so and stays signed in, rather
than claiming a sign-out that didn't happen.

## 8. The microphone

Voice logging arrives in phase 6. The app is already allowed to ask for the
microphone (`Permissions-Policy: microphone=(self)`), and nothing else. On an
iPhone, the installed app asks for permission separately from Safari, the
first time you tap the mic. If you said no, look under Safari's website
settings in the Settings app, or remove the app from the home screen and add
it again.

## 9. Working on the UI locally

```bash
make web-config   # writes web/config.js from the stack outputs (git ignores it)
make serve        # http://localhost:8000
```

Only the static files are served locally; there is no API at
`localhost:8000/v1`. The sign-in screen renders, but signing in stops at the
step that sets the cookie, because that needs the real API on the same origin.
For anything past sign-in, deploy and use the `AppUrl`. The service worker is
off on `localhost`, so your edits show up on reload.

## 10. Optional: a custom domain

Not set up by this stack. It needs:

- a domain (Route 53 sells `.com` for about $15 a year; check the console for
  the current price),
- a Route 53 hosted zone ($0.50 a month),
- a free ACM certificate in `us-east-1`,
- `Aliases` and the certificate on the distribution in `template.yaml`, and
  `AppOrigin` set to the new origin (`deploy-backend.sh` reads it from the
  `AppUrl` output, so that output would change too).

Everyone has to sign in again afterwards: the cookie belongs to the old
domain.

---

## Common errors

| What you see | Fix |
| --- | --- |
| `web/ has uncommitted changes` | Commit, then `make deploy-web` |
| `not signed in to AWS` | `aws sso login`, or check `AWS_PROFILE` (doc 02) |
| `stack 'workout-log' not found` | Deploy the backend first: `make deploy` (doc 04) |
| The page says "This copy of the app has no config.js" | Locally: `make web-config`. Deployed: run `make deploy-web` again; it writes `config.js` every time |
| Sign-in shows "This request must come from the app itself." | `AppOrigin` isn't set, so the cookie routes refuse everything. Run `make deploy` again; its second pass sets it (doc 04) |
| Sign-in shows "Can't reach sign-in. Check your connection." | You're offline, or the browser blocked the call to Cognito. Open the browser console: a CSP error naming `cognito-idp` means the region in `config.js` doesn't match the stack's. Run `make deploy-web` again |
| "Incorrect email or password." on the first sign-in | Use the temporary password from the invite email, exactly. Cognito also locks an account for a while after five wrong passwords |
| "That temporary password has expired." | It lasts 7 days. Resend the invite (doc 05, "Common errors") |
| The installed iPhone app asks for the password every time it opens | Make sure you signed in inside the installed app, not Safari. In the Settings app, Safari's **Block All Cookies** must be off |
| The app still shows the old version | Wait for the invalidation (1-2 minutes), then close and reopen the app. On a laptop, a hard reload (Cmd-Shift-R) |
| "Couldn't load the log" with a red dot | The API answered with an error. `make smoke`, then the function's logs (doc 04, "Reading the logs") |
| `AccessDenied` from `create-invalidation` | The AWS login lacks CloudFront permissions. The deploy login from doc 02 has them |

---

Next: the Day tab arrives in phase 4. To check the backend from outside at any
time: `make smoke`.
