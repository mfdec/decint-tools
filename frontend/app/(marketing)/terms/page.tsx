import { SiteNav } from "@/components/site/SiteNav";
import { SiteFooter } from "@/components/site/SiteFooter";
import {
  CONTACT_EMAIL, LEGAL_COURTS, LEGAL_ENTITY, LEGAL_JURISDICTION, TERMS_VERSION,
} from "@/lib/site";

export const metadata = {
  title: "Data Access Purchase Agreement — DECINT",
  description:
    "The terms of buying access to DECINT, including when a purchase can be refunded.",
};

// Refund windows quoted in sections 5.4 and 7. Change them here, then bump
// TERMS_VERSION (lib/site.ts and backend/app/routers/billing.py).
const UNUSED_ACCESS_HOURS = 24;
const NOT_DELIVERED_BUSINESS_DAYS = 3;
const NOT_AS_DESCRIBED_DAYS = 7;

const h2 = { fontSize: 20, margin: "36px 0 10px" } as const;
const p = {
  fontSize: 14.5,
  lineHeight: 1.65,
  color: "var(--color-neutral-300)",
  margin: "0 0 12px",
} as const;
const list = { ...p, paddingLeft: 22 } as const;

export default function TermsPage() {
  const seller = LEGAL_ENTITY || "the operator of DECINT (decint.tools)";

  return (
    <main style={{ minHeight: "100vh" }}>
      <SiteNav />

      <article style={{ maxWidth: 760, margin: "0 auto", padding: "76px 24px 76px" }}>
        <p className="eyebrow">Terms</p>
        <h1 style={{ fontSize: "clamp(30px, 5vw, 40px)", margin: "0 0 10px" }}>
          Data Access Purchase Agreement
        </h1>
        <p style={{ ...p, color: "var(--color-neutral-500)" }}>Version {TERMS_VERSION}</p>
        <p style={p}>
          This agreement sets the terms on which you buy access to data from us,
          including that access cannot be bought and then refunded once the data
          has been delivered.
        </p>

        <h2 style={h2}>1. Parties and definitions</h2>
        <p style={p}>
          This Data Access Purchase Agreement (&quot;Agreement&quot;) is between{" "}
          <strong>{seller}</strong> (&quot;Seller&quot;, &quot;we&quot;, &quot;us&quot;) and the person or
          entity that completes a Purchase (&quot;Buyer&quot;, &quot;you&quot;). By completing a
          Purchase, you accept this Agreement.
        </p>
        <p style={p}>In this Agreement:</p>
        <ul style={list}>
          <li>
            <strong>&quot;Data&quot;</strong> means the datasets, records, reports, exports and API
            responses made available through DECINT.
          </li>
          <li>
            <strong>&quot;Access&quot;</strong> means the right to view, query, download or export
            Data, whether through an account, an API key, a download link or any
            other means.
          </li>
          <li>
            <strong>&quot;Purchase&quot;</strong> means any payment for Access, including one-time
            purchases, subscriptions, credits and bundles.
          </li>
          <li>
            <strong>&quot;Access Delivered&quot;</strong> means the moment we make Data available to
            you, for example by unlocking it in your account, activating an API
            key or credits, or sending a download link. It does not matter whether
            you then use it.
          </li>
          <li>
            <strong>&quot;Data Retrieved&quot;</strong> means that you have viewed, queried,
            downloaded, exported, copied or otherwise obtained any part of the
            Data.
          </li>
        </ul>

        <h2 style={h2}>2. Purchase and license</h2>
        <p style={p}>
          You are buying a limited license to use Data, not ownership of it. On
          completing a Purchase and payment, we grant you a non-exclusive,
          non-transferable, non-sublicensable right to use the Data you paid for,
          for the period and scope shown at checkout.
        </p>
        <p style={p}>
          Data is a digital product. It is delivered electronically and, in
          almost every case, immediately after payment is confirmed. Once Data is
          delivered, we cannot take it back from you, so Access Delivered is the
          point at which the Purchase is treated as fulfilled.
        </p>

        <h2 style={h2}>3. Fees and payment</h2>
        <p style={p}>
          You agree to pay the price shown at checkout, plus any applicable
          taxes. Card payments are processed by Stripe and crypto payments by
          NOWPayments, and we do not store your full card details. Prices may
          change for future Purchases, but a change never affects a Purchase you
          have already completed.
        </p>
        <p style={p}>
          Card subscriptions renew automatically until you cancel. Crypto buys a
          fixed prepaid period and does not renew. You confirm that you are
          authorised to use the payment method you submit, and that the billing
          details you give us are accurate.
        </p>

        <h2 style={h2}>4. Immediate delivery and waiver of withdrawal rights</h2>
        <p style={p}>
          By completing a Purchase, you expressly ask us to deliver the Data
          immediately, and you acknowledge that:
        </p>
        <ol style={list}>
          <li>Access Delivered begins as soon as payment is confirmed.</li>
          <li>
            Data cannot be returned once delivered, because a copy of it stays
            with you.
          </li>
          <li>
            Where the law gives consumers a right to cancel a distance purchase
            (for example a 14-day withdrawal period), that right is lost once we
            have started delivering digital content with your prior express
            consent and acknowledgement, to the extent the law allows this.
          </li>
        </ol>
        <p style={p}>
          We show this acknowledgement at checkout, and you must confirm it before
          you can pay.
        </p>

        <h2 style={h2}>5. No refunds after access is delivered</h2>
        <p style={p}>
          <strong>5.1 All sales are final.</strong> Once Access Delivered has
          occurred, your Purchase is non-refundable, except as set out in
          Section 7.
        </p>
        <p style={p}>
          <strong>5.2 Purchase-then-refund is not permitted.</strong> You may not
          buy Access, retrieve the Data, and then ask for your money back. If you
          have retrieved any Data, you are not entitled to a refund, in whole or
          in part, for any reason other than those listed in Section 7. This
          applies whether you ask us directly, ask your bank or card issuer, or
          use any other refund or dispute route.
        </p>
        <p style={p}>
          <strong>5.3 Why this rule exists.</strong> Data cannot be un-delivered.
          A buyer who has already viewed, downloaded or exported it has received
          the full value of the Purchase, and a refund would let them keep that
          value for free.
        </p>
        <p style={p}>
          <strong>5.4 Unused Access.</strong> If Access Delivered has occurred but
          you have not yet retrieved any Data, we may, at our sole discretion,
          offer a refund if you ask within <strong>{UNUSED_ACCESS_HOURS} hours</strong>{" "}
          of Purchase. We are not obliged to, and you may not rely on this clause
          once any Data has been retrieved.
        </p>
        <p style={p}>
          <strong>5.5 How we decide.</strong> We determine whether Data has been
          retrieved from our access logs, download records and API usage records.
          Our records are conclusive unless you show clear evidence that they are
          wrong.
        </p>
        <p style={p}>
          <strong>5.6 Repeat attempts.</strong> If you make more than one
          Purchase-then-refund attempt, or one that we reasonably believe was
          intended to obtain Data without paying, we may refuse the refund, close
          your account and refuse future Purchases.
        </p>

        <h2 style={h2}>6. Chargebacks, disputes and enforcement</h2>
        <p style={p}>
          <strong>6.1 Contact us first.</strong> If you think a charge is wrong,
          email <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a> before
          contacting your bank. We will review it promptly.
        </p>
        <p style={p}>
          <strong>6.2 Chargebacks.</strong> If you file a chargeback or payment
          dispute for a Purchase after you have retrieved Data, you agree that the
          dispute is a breach of this Agreement. We may contest it and give the
          payment processor your Purchase records, access logs and this Agreement
          as evidence.
        </p>
        <p style={p}>
          <strong>6.3 Consequences.</strong> If you obtain a refund or chargeback
          in breach of Section 5, we may, without notice:
        </p>
        <ul style={list}>
          <li>revoke all Access and disable your account and any API keys;</li>
          <li>refuse future Purchases from you;</li>
          <li>
            claim back the amount of the refund or chargeback, along with any fees
            the processor charges us for it; and
          </li>
          <li>require you to delete the Data you retrieved and stop using it.</li>
        </ul>
        <p style={p}>
          <strong>6.4 Data stays licensed only while paid for.</strong> A refund or
          chargeback ends your license to the Data. Keeping or using Data after a
          refund is unlicensed use and an infringement of our rights.
        </p>

        <h2 style={h2}>7. Limited exceptions</h2>
        <p style={p}>
          Section 5 does not stop us from giving a refund, and we will give one,
          in these cases:
        </p>
        <ol style={list}>
          <li>
            <strong>Duplicate or incorrect charge.</strong> You were charged twice
            for the same Purchase, or charged an amount different from the one
            shown at checkout.
          </li>
          <li>
            <strong>Access not delivered.</strong> You paid but Access Delivered
            did not occur, and we cannot fix this within{" "}
            <strong>{NOT_DELIVERED_BUSINESS_DAYS} business days</strong> of your
            report.
          </li>
          <li>
            <strong>Materially not as described.</strong> The Data is substantially
            different from the description shown at checkout, and you report this
            within <strong>{NOT_AS_DESCRIBED_DAYS} days</strong> of Purchase.
          </li>
          <li>
            <strong>Unauthorised use of your payment method,</strong> reported to
            us promptly, where you did not retrieve the Data.
          </li>
          <li>
            <strong>Law requires it.</strong> A refund is required by consumer
            protection law that cannot be waived by agreement.
          </li>
        </ol>
        <p style={p}>
          We may ask for reasonable evidence for any of these. A refund under this
          Section ends your Access to the Data concerned.
        </p>

        <h2 style={h2}>8. Use, ownership and liability</h2>
        <p style={p}>
          <strong>8.1 Permitted use.</strong> You may use the Data for lawful
          purposes, for yourself or for your own organisation&apos;s internal use. You
          may not resell, republish, sublicense or share the Data or your
          credentials with others, and you may not use automated means to copy
          more Data than your Purchase covers.
        </p>
        <p style={p}>
          <strong>8.2 Ownership.</strong> We and our licensors keep all rights in
          the Data. Nothing in this Agreement transfers ownership to you.
        </p>
        <p style={p}>
          <strong>8.3 Disclaimer.</strong> The Data is provided &quot;as is&quot;. We work to
          keep it accurate and current, but we do not promise that it is complete,
          error-free or fit for a particular purpose, and you use it at your own
          risk.
        </p>
        <p style={p}>
          <strong>8.4 Limit of liability.</strong> To the extent the law allows,
          our total liability to you for any claim about a Purchase is limited to
          the amount you paid for that Purchase, and we are not liable for
          indirect or consequential loss, including lost profit. Nothing in this
          Agreement limits liability that cannot be limited by law.
        </p>

        <h2 style={h2}>9. General terms and acceptance</h2>
        <p style={p}>
          <strong>9.1 Governing law.</strong>{" "}
          {LEGAL_JURISDICTION ? (
            <>
              This Agreement is governed by the laws of{" "}
              <strong>{LEGAL_JURISDICTION}</strong>
              {LEGAL_COURTS && (
                <>
                  , and the courts of <strong>{LEGAL_COURTS}</strong> have
                  jurisdiction
                </>
              )}
              , except where consumer law gives you the right to bring a claim in
              your home country.
            </>
          ) : (
            <>
              This Agreement is governed by the laws that apply where the Seller is
              established, except where consumer law gives you the right to rely on
              the law of, or bring a claim in, your home country.
            </>
          )}
        </p>
        <p style={p}>
          <strong>9.2 Changes.</strong> We may update this Agreement for future
          Purchases. The version in force when you complete a Purchase applies to
          that Purchase.
        </p>
        <p style={p}>
          <strong>9.3 Entire agreement.</strong> This Agreement is the whole
          agreement about Purchases. If a part of it is found unenforceable, the
          rest stays in effect.
        </p>
        <p style={p}>
          <strong>9.4 Contact.</strong> Questions and refund requests go to{" "}
          <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>.
        </p>
        <p style={p}>
          <strong>9.5 Acceptance.</strong> Before paying, you must tick the box at
          checkout that reads:
        </p>
        <blockquote
          style={{
            ...p,
            margin: "0 0 12px",
            padding: "4px 0 4px 16px",
            borderLeft: "3px solid var(--color-divider)",
            color: "var(--color-neutral-400)",
          }}
        >
          I have read and agree to the Data Access Purchase Agreement. I ask for the
          data to be delivered immediately, and I understand that once I have
          retrieved any data, I am not entitled to a refund except as set out in
          Section 7.
        </blockquote>
      </article>

      <SiteFooter />
    </main>
  );
}
