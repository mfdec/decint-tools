import Link from "next/link";
import { DocPage, DocSection } from "@/components/site/DocPage";
import { CONTACT_EMAIL } from "@/lib/site";

export const metadata = {
  title: "Delete your account — DECINT",
  description: "How to delete your DECINT account, what is removed, and what is kept.",
};

// Public on purpose: app stores ask for a deletion URL that works without the
// app and without signing in. What it lists must match users.delete() in
// backend/app/services/users.py.
export default function DeleteAccountPage() {
  return (
    <DocPage eyebrow="Account" title="Deleting your account.">
      <p>
        You can delete your DECINT account yourself at any time, from the Android
        app or the website. It happens immediately and can&apos;t be undone.
      </p>

      <DocSection title="How">
        <ul>
          <li><Link href="/login?next=/account">Sign in</Link> and open your account page (your username, top right of the console).</li>
          <li>Under <strong>Delete account</strong>, enter your current password and type <strong>DELETE</strong>.</li>
          <li>Press <strong>Delete my account</strong>. You are signed out, and a confirmation goes to your email address.</li>
        </ul>
        <p>
          Can&apos;t sign in? Email <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a> from
          the address on the account and we will delete it for you.
        </p>
      </DocSection>

      <DocSection title="What is deleted">
        <ul>
          <li>Your profile: email address, username, password hash and two-factor settings.</li>
          <li>Every sign-in session, sign-in code, login token and password-reset link.</li>
          <li>Your plan, search allowance and payment history.</li>
          <li>Your support tickets and their messages.</li>
        </ul>
        <p>
          A card subscription is cancelled before the account is deleted, so it will
          not renew. If it can&apos;t be cancelled at that moment, nothing is deleted
          and you are asked to try again.
        </p>
      </DocSection>

      <DocSection title="What is kept">
        <ul>
          <li>
            <strong>Security log entries</strong> (the action, your email address and the
            IP address involved), kept to investigate abuse of accounts.
          </li>
          <li>
            <strong>Records held by payment processors.</strong> Stripe and NOWPayments
            keep their own records of past payments, as the law requires them to.
          </li>
          <li>
            <strong>Visit statistics</strong>, which are not linked to your account,
            are deleted after 90 days as usual.
          </li>
        </ul>
        <p>
          More in the <Link href="/privacy">privacy policy</Link>.
        </p>
      </DocSection>
    </DocPage>
  );
}
