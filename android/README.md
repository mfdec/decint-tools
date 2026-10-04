# DECINT for Android

The Android app for decint.tools. It is a small native shell (Kotlin, no
third-party libraries beyond AndroidX) that runs the site in a locked-down
WebView, so every console feature works in the app the day it ships on the site,
and a site update reaches the app without a new build.

```
app/src/main/java/tools/decint/app/
  MainActivity.kt         splash, edge-to-edge insets, back button, offline screen,
                          deep links, file picker, renderer-crash recovery
  UrlPolicy.kt            what stays in the app, what opens outside, what is blocked
  DecintWebViewClient.kt  applies UrlPolicy; blocks ad hosts; main-frame errors
  DecintChromeClient.kt   progress, file chooser; denies camera/mic/location
  Downloads.kt            plain downloads via DownloadManager; blob: exports via
                          a page script → MediaStore Downloads
  FileNames.kt            safe file names for anything saved
```

## How it behaves

- **Opens on `/console`.** The site's own middleware sends anyone without a session
  to `/login?next=/console`. The session cookie persists like in a browser
  (`SESSION_TTL_SECONDS`, 12 hours by default).
- **Stays on the site.** decint.tools and www.decint.tools load in the app. Other
  links open in the right app: browser, mail, phone, or Tor Browser for `.onion`
  results. `javascript:`, `file:`, `content:` and `intent:` navigations are refused.
- **No ads, no purchases.** AdSense is blocked at the network layer, because its rules
  forbid ads in app WebViews. The app adds `DecintAndroid/<version>` to its user
  agent and marks the page `html.in-app`. The site then hides every checkout,
  plan-change and "Pricing" control, because Google Play doesn't allow selling
  digital plans outside Play Billing (`frontend/lib/platform.ts`). Existing paid
  plans keep working in the app.
- **Files.**
  - The dark-web JSON/CSV export and the admin CSV exports land in Downloads.
  - The admin leak-dataset upload opens the system file picker.
- **Locked down.**
  - HTTPS only, system CAs only. A debug build also allows `10.0.2.2` for a dev server.
  - No file or content access from the page.
  - Safe Browsing on.
  - Every web permission request denied.
  - No backup of app data.
  - The page-to-app bridge (`DecintNative`) is offered only to the site's own
    origin, and only to the main frame.

## Building

**On GitHub (no setup):** `.github/workflows/android.yml` runs on every push to
`main` that touches `android/`. Each run's page has:

- `decint-android-debug-apk`: install this on a phone to test.
- `decint-android-release`: the `.aab` for Google Play, plus a release `.apk`.

To attach the files to a GitHub Release, run the workflow manually: Actions →
Android → Run workflow, then set a tag such as `android-v1.0.0`.

**Locally:** you need JDK 17+ and the Android SDK, either through Android Studio
(open the `android/` folder) or the command-line tools.

```bash
cd android
./gradlew testDebugUnitTest assembleDebug          # app/build/outputs/apk/debug/
./gradlew bundleRelease                            # app/build/outputs/bundle/release/
./gradlew assembleDebug -PdecintBaseUrl=http://10.0.2.2:3000   # emulator → local `serve dev`
```

`-PdecintBaseUrl` points a build at another deployment. The deep-link host
follows it.

## Signing for Play

Play signs the app it delivers ("Play App Signing"). You sign uploads with an
**upload key** that you create once and keep safe:

```bash
keytool -genkeypair -v -keystore decint-upload.jks -alias upload \
  -keyalg RSA -keysize 4096 -validity 10000
base64 -w0 decint-upload.jks   # paste the output into the secret below
```

Add these as repository secrets (Settings → Secrets and variables → Actions):

| secret | value |
|---|---|
| `ANDROID_UPLOAD_KEYSTORE_B64` | the base64 output above |
| `ANDROID_UPLOAD_KEYSTORE_PASSWORD` | the keystore password |
| `ANDROID_UPLOAD_KEY_ALIAS` | `upload` |
| `ANDROID_UPLOAD_KEY_PASSWORD` | the key password |

Without them, CI still builds, but the release outputs are unsigned. Never
commit the `.jks`: `.gitignore` excludes it.

## Testing on a phone

Install the debug APK. It installs as `tools.decint.app.debug`, beside a Play
install. Then check:

1. Sign in, including the captcha and a 2FA code. Then sign out.
2. Every console app opens, and the "⋯" menu works: switch app, Support, Profile, Sign out.
3. Run a dark-web search, export JSON and CSV, and confirm both files are in Downloads.
4. As an admin: export a CSV from Admin → Data, and upload a leak dataset (file picker).
5. Pricing and billing show no buy or manage buttons. The console's plan chip still shows the plan.
6. A Discord invite or `.onion` result opens outside the app.
7. With airplane mode on, launch the app and see the offline screen. Turn
   airplane mode off and press "Try again".
8. The back button walks back through pages, then leaves the app.
9. Account → Delete account works on a throwaway account.

## Google Play release checklist

1. **Create the app** in Play Console: "DECINT", app (not game), free. Leave
   **Play App Signing** on.
2. **First upload by hand:** take the `.aab` from a CI run that had the signing
   secrets, and upload it to *Internal testing*. Play needs the first one through
   the Console.
3. **App content:**
   - Privacy policy: `https://decint.tools/privacy`
   - Account deletion URL: `https://decint.tools/delete-account`. In-app deletion
     is on the Account page.
   - Ads: **No**. The app blocks the website's AdSense.
   - Target audience: **18+**.
   - Content rating: fill in the questionnaire.
   - App access: give reviewers a working login, and make sure captcha won't block it.
     Paid-plan features need a paid account.
   - Data safety. These answers follow `/privacy`; check them against your deployment:

     | data type | collected | purpose |
     |---|---|---|
     | Email address | yes, required | account management |
     | User IDs (username) | yes, required | account management |
     | Phone number | yes, optional (SMS 2FA only) | account security |
     | Approximate location (looked up from the IP on the server) | yes | analytics |
     | App interactions (page views) | yes | analytics |
     | Purchase history (website purchases) | yes | account management |
     | Other user-generated content (support tickets) | yes | customer support |

     Data is encrypted in transit, and users can request deletion. Searches go
     to the leak and onion sources as a direct result of the user's own search,
     which Play treats as user-initiated, not "sharing".
4. **Store listing:**
   - Icon: `frontend/public/icon-512.png`
   - Feature graphic: 1024×500, still to be made
   - At least two phone screenshots
5. **Digital Asset Links.** Email links (activation, password reset, email change)
   open in the app instead of the browser once this is done:
   1. Play Console → *Test and release → App integrity → App signing*. Copy the
      SHA-256 of the **app signing key**.
   2. Create `frontend/public/.well-known/assetlinks.json`:

      ```json
      [{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {
          "namespace": "android_app",
          "package_name": "tools.decint.app",
          "sha256_cert_fingerprints": ["AA:BB:…the fingerprint…"]
        }
      }]
      ```

   3. Deploy, then check it at
      `https://digitalassetlinks.googleapis.com/v1/statements:list?source.web.site=https://decint.tools&relation=delegate_permission/common.handle_all_urls`
6. **Expect review questions.** Leak search shows revealed passwords on paid plans,
   and dark-web search queries onion indexes. Be ready to describe the app's
   investigative and defensive purpose. If the review objects, the usual fix is to
   turn the flagged feature off for the app's user agent.

## Versions

AGP 8.13, Kotlin 2.3 and Gradle 8.14 (`gradle/libs.versions.toml`, wrapper pinned
by SHA-256). `compileSdk`/`targetSdk` 36, `minSdk` 29 (Android 10). The
`applicationId` `tools.decint.app` can't be changed once the app is on Play.
