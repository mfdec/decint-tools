import Link from "next/link";
import { DocPage, DocSection } from "@/components/site/DocPage";
import { CONTACT_EMAIL } from "@/lib/site";

export const metadata = {
  title: "Privacy policy — DECINT",
  description: "What DECINT stores about you, who it is shared with, how long it is kept, and how to delete it.",
};

// Written from what the code actually stores (backend/app/db.py and the
// services that write to it). When a table, a processor or a retention
// default changes, this page has to change with it — the Play Store listing
// links here.
export default function PrivacyPage() {
  return (
    <DocPage eyebrow="Legal" title="Privacy policy." updated="2026-10-04">
      <p>
        This policy covers decint.tools and the DECINT Android app, which shows the
        same site. It says what we store about you, who else sees any of it, how
        long it is kept, and how to have it deleted. Questions go
        to <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>.
      </p>

      <DocSection title="What we store">
        <ul>
          <li>
            <strong>Your account:</strong> email address, username, a one-way hash
            of your password (Argon2id, never the password itself), your role and
            plan, and your two-factor settings. If you turn on SMS codes, that
            includes your phone number.
          </li>
          <li>
            <strong>Sign-in sessions:</strong> the IP address and browser user agent of
            each signed-in session, so you and we can see where an account is in use.
            A session lasts 12 hours.
          </li>
          <li>
            <strong>Search allowance:</strong> how many searches your account has made
            in the current period, to apply your plan&apos;s limit. <strong>What you
            search for is not stored.</strong>
          </li>
          <li>
            <strong>Visit statistics:</strong> for each page view, the IP address,
            the approximate location looked up from it on our own server (no
            third-party location service), browser and device details (type, screen
            size, language, time zone), the page, and the referring page. Visitors
            are counted with a hash that changes every day, not with a cookie.
          </li>
          <li>
            <strong>Payments:</strong> your plan, its renewal date, and a history of
            payments (amount, date, processor reference). Card numbers go straight
            to Stripe, and Google Play handles payment for subscriptions bought in
            the Android app; neither reaches us.
          </li>
          <li>
            <strong>Support tickets:</strong> the messages you send us and our replies.
          </li>
          <li>
            <strong>Security log:</strong> sign-ins, failed attempts, password and
            email changes and account deletions, with the email address and IP
            address involved.
          </li>
        </ul>
      </DocSection>

      <DocSection title="Who else sees it">
        <ul>
          <li>
            <strong>The sources your searches go to.</strong> A leak search sends your
            query to XposedOrNot, ProxyNova, LeakCheck and the Have I Been Pwned breach
            catalogue. A dark-web search sends it to onion search engines, over Tor or
            through their public gateways. An IP lookup sends the address to the
            regional internet registry that holds it, through rdap.org; its location
            and network are looked up on our own server. They receive the query, not
            your account details.
          </li>
          <li>
            <strong>Payment processors:</strong> Stripe for cards and NOWPayments for
            cryptocurrency on the website, and Google Play for subscriptions in the
            Android app, each under its own privacy policy. For Play purchases we
            receive the subscription&apos;s status and order number, not your
            payment details.
          </li>
          <li>
            <strong>Bot protection:</strong> Google reCAPTCHA or hCaptcha on sign-up and
            sign-in.
          </li>
          <li>
            <strong>Email and SMS delivery:</strong> our mail provider sends account
            emails (activation, sign-in codes, password resets, receipts). Twilio
            sends SMS codes, if you choose them.
          </li>
          <li>
            <strong>Advertising, on the website only:</strong> decint.tools shows Google
            AdSense ads, and Google may set cookies to serve them. The Android app
            blocks these ads.
          </li>
        </ul>
        <p>We do not sell your data, and nobody else gets it unless the law requires us to hand it over.</p>
      </DocSection>

      <DocSection title="Cookies">
        <p>
          We set one cookie, <code>decint_session</code>, which keeps you signed in.
          The captcha and, on the website, Google AdSense may set their own.
        </p>
      </DocSection>

      <DocSection title="How long we keep it">
        <ul>
          <li>Account, payment history and support tickets: until you delete your account.</li>
          <li>Sign-in sessions: 12 hours, or until you sign out.</li>
          <li>Visit statistics: 90 days. After that, only daily totals remain, which describe no one.</li>
          <li>Security log: kept to investigate abuse of accounts, including deleted ones.</li>
        </ul>
      </DocSection>

      <DocSection title="Your choices">
        <p>
          You can change your email and password on your account page, and delete
          your account from the same page, in the app or on the website.
          See <Link href="/delete-account">deleting your account</Link> for what is
          removed. For a copy of your data, or to correct it, email us
          at <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a> from the address
          on the account.
        </p>
      </DocSection>

      <DocSection title="Security">
        <p>
          Every connection is HTTPS. Passwords are stored only as Argon2id hashes,
          and sign-in links and tokens only as keyed hashes. Two-factor sign-in is
          available on every account.
        </p>
      </DocSection>

      <DocSection title="Children">
        <p>DECINT is for adults. It is not meant for anyone under 18, and we do not knowingly hold their data.</p>
      </DocSection>

      <DocSection title="Changes">
        <p>When this policy changes, the date at the top changes with it.</p>
      </DocSection>
    </DocPage>
  );
}
