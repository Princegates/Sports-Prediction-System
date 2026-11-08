import { Link } from "react-router-dom";
import { LegalContact } from "../components/LegalContact";
import { PublicShell } from "../components/PublicShell";
import { LEGAL } from "../lib/legal";

/**
 * Refund Policy. What is sold here is digital and switched on the moment a
 * payment is confirmed, so the policy is built around that: no refunds for
 * something already delivered, but a clear promise to fix payments that go
 * wrong. Have it reviewed by a lawyer before relying on it.
 */

const PRODUCTS: { name: string; rule: string }[] = [
  {
    name: "Access time (for example 7 or 30 days)",
    rule: "Starts as soon as payment is confirmed. Not refundable once it has started, except in the cases listed below.",
  },
  {
    name: "Packs of extra AI Generation runs",
    rule: "Refundable only if the runs were never added to your account or none have been used. Runs already used are not refundable.",
  },
  {
    name: "24-hour unlimited AI Generation pass",
    rule: "Not refundable once it has started.",
  },
  {
    name: "Access codes bought from an admin (for example on WhatsApp)",
    rule: "Same rules as access time. Not refundable once the code has been redeemed.",
  },
];

export function Refunds() {
  return (
    <PublicShell
      title="Refund Policy — Socca Intelligence"
      description="When Socca Intelligence refunds a payment and how to ask: failed, duplicate or wrong payments are fixed or refunded; access already delivered is not."
    >
      <section className="doc-hero">
        <span className="eyebrow">Legal</span>
        <h1>Refund Policy</h1>
        <p className="lede">
          What you buy on {LEGAL.site} is digital and works straight away, so we can't take it back once
          you have it. But if a payment goes wrong, we will fix it or refund you. Last updated {LEGAL.updated}.
        </p>
      </section>

      <section className="doc-section">
        <div className="doc-callout">
          <h4>In short</h4>
          <ul>
            <li>
              <strong>We will fix or refund:</strong> a payment that was taken but did not give you access, a
              duplicate payment, a wrong amount, or a charge you did not make.
            </li>
            <li>
              <strong>We will not refund:</strong> access or runs that are already active or used, or
              because a prediction or slip did not win.
            </li>
            <li>
              <strong>Ask within {LEGAL.refundWindowDays} days</strong> of paying, and have your payment
              reference ready.
            </li>
          </ul>
        </div>
      </section>

      <section className="doc-section">
        <h2>What each purchase covers</h2>
        <div className="doc-table-wrap">
          <table className="doc-table">
            <thead>
              <tr>
                <th>Purchase</th>
                <th>Refund rule</th>
              </tr>
            </thead>
            <tbody>
              {PRODUCTS.map((p) => (
                <tr key={p.name}>
                  <td>
                    <strong>{p.name}</strong>
                  </td>
                  <td>{p.rule}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p>The free trial costs nothing, so there is nothing to refund.</p>
      </section>

      <section className="doc-section">
        <h2>When we will refund you</h2>
        <ul className="doc-list">
          <li>
            <strong>Your payment went through but access or runs did not appear.</strong> We will first try to
            fix it and add what you paid for. If we cannot, we refund you.
          </li>
          <li>
            <strong>You were charged twice</strong> for the same thing. We refund the extra payment.
          </li>
          <li>
            <strong>You paid the wrong amount</strong> because of a mistake on our side.
          </li>
          <li>
            <strong>You did not make the payment.</strong> Tell us straight away so we can look into it.
          </li>
          <li>
            <strong>The service could not be provided</strong> at all because of a fault on our side, and we could not fix it.
          </li>
        </ul>
      </section>

      <section className="doc-section">
        <h2>When we will not refund</h2>
        <ul className="doc-list">
          <li>The access time has started, or the runs or pass have been used.</li>
          <li>
            A prediction, pick or slip did not win. Predictions are chances, never guarantees (see our{" "}
            <Link to="/terms">Terms</Link>).
          </li>
          <li>You changed your mind, or you did not read what was included.</li>
          <li>Your account was suspended or closed for breaking our <Link to="/terms">Terms</Link>.</li>
          <li>The problem came from your phone network, wallet or bank, and the payment never reached us.</li>
        </ul>
        <p>Nothing in this policy limits any right you have by law.</p>
      </section>

      <section className="doc-section">
        <h2>How to ask for a refund</h2>
        <ol className="doc-list">
          <li>
            Contact us on WhatsApp or email within {LEGAL.refundWindowDays} days of the payment (details below).
          </li>
          <li>
            Send your <strong>payment reference</strong> (it is shown on the Access page after you pay), the
            phone number or card you paid with, the amount, and the date and time.
          </li>
          <li>
            We check the payment with Hubtel and tell you what we found, usually within 2 working days.
          </li>
          <li>
            If you are due a refund, we send it back to the way you paid (mobile money or card). This usually
            takes 5 to 10 working days after we approve it, depending on your network or bank.
          </li>
        </ol>
        <p>
          If a payment is stuck, an admin can also fix your access by hand, without waiting for a refund.
        </p>
      </section>

      <section className="doc-section">
        <h2>Changes to this policy</h2>
        <p>
          If we change this policy, we update this page and the date at the top. A change applies to
          purchases made after it.
        </p>
      </section>

      <LegalContact topic="Refund Policy" />
    </PublicShell>
  );
}
