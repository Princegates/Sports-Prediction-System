import { Link } from "react-router-dom";
import { LegalContact } from "../components/LegalContact";
import { PublicShell } from "../components/PublicShell";
import { LEGAL } from "../lib/legal";

/**
 * No-Refund Policy. Everything sold here is digital and switched on the moment
 * a payment is confirmed, so every sale is final. The one thing we do promise
 * is to put right a payment that went wrong by delivering what was paid for
 * (that is fixing our service, not refunding money). Have it reviewed by a
 * lawyer before relying on it -- consumer-protection law in Ghana may give
 * buyers rights this page cannot remove.
 */

const PRODUCTS: { name: string; rule: string }[] = [
  {
    name: "Access time (for example 7 or 30 days)",
    rule: "Starts as soon as payment is confirmed. Final sale, no refund.",
  },
  {
    name: "Packs of extra AI Generation runs",
    rule: "Final sale, no refund, whether the runs are used or not.",
  },
  {
    name: "24-hour unlimited AI Generation pass",
    rule: "Final sale, no refund.",
  },
  {
    name: "Access codes bought from an admin (for example on WhatsApp)",
    rule: "Final sale, no refund, whether the code has been redeemed or not.",
  },
];

export function Refunds() {
  return (
    <PublicShell
      title="No-Refund Policy — Socca Intelligence"
      description="All sales on Socca Intelligence are final and not refundable. If a payment goes wrong we fix your access instead."
    >
      <section className="doc-hero">
        <span className="eyebrow">Legal</span>
        <h1>No-Refund Policy</h1>
        <p className="lede">
          Everything you buy on {LEGAL.site} is digital and works straight away, so <strong>all sales are
          final and we do not give refunds</strong>. Please check what you are buying before you pay. Last
          updated {LEGAL.updated}.
        </p>
      </section>

      <section className="doc-section">
        <div className="doc-callout">
          <h4>In short</h4>
          <ul>
            <li>
              <strong>No refunds.</strong> Once you pay, the purchase is final, including if you change your
              mind or a prediction or slip does not win.
            </li>
            <li>
              <strong>We will still fix mistakes.</strong> If a payment goes through but your access or runs
              do not appear, we add what you paid for.
            </li>
            <li>
              <strong>Tell us within {LEGAL.problemWindowDays} days</strong> and have your payment reference
              ready.
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
                <th>Rule</th>
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
        <p>The free trial costs nothing, so there is nothing to pay for or refund.</p>
      </section>

      <section className="doc-section">
        <h2>Why we do not refund</h2>
        <ul className="doc-list">
          <li>Access, runs and passes are digital. They are switched on the moment Hubtel confirms your payment and cannot be taken back.</li>
          <li>
            Predictions are chances, never guarantees (see our <Link to="/terms">Terms</Link>). A pick or
            slip that does not win is not a fault in the service.
          </li>
          <li>Every price and what it includes is shown before you pay, and nothing renews automatically.</li>
        </ul>
      </section>

      <section className="doc-section">
        <h2>If a payment goes wrong</h2>
        <p>
          We do not refund, but we do not leave you without what you paid for. We will fix it by adding the
          access or runs to your account:
        </p>
        <ul className="doc-list">
          <li>
            <strong>You paid but nothing appeared.</strong> We check the payment with Hubtel and add what you paid for.
          </li>
          <li>
            <strong>You were charged twice</strong> for the same thing. We add the extra payment to your
            account as more access time or runs, so it is not lost.
          </li>
          <li>
            <strong>You did not make the payment.</strong> Tell us straight away so we can look into it. You
            can also report it to your mobile money provider or bank.
          </li>
        </ul>
        <p>Nothing in this policy limits any right you have by law.</p>
      </section>

      <section className="doc-section">
        <h2>How to report a payment problem</h2>
        <ol className="doc-list">
          <li>
            Contact us on WhatsApp or email within {LEGAL.problemWindowDays} days of the payment (details below).
          </li>
          <li>
            Send your <strong>payment reference</strong> (it is shown on the Access page after you pay), the
            phone number or card you paid with, the amount, and the date and time.
          </li>
          <li>
            We check the payment with Hubtel and tell you what we found, usually within 2 working days.
          </li>
          <li>If the payment was made and your access is missing, an admin adds it to your account.</li>
        </ol>
      </section>

      <section className="doc-section">
        <h2>Changes to this policy</h2>
        <p>
          If we change this policy, we update this page and the date at the top. A change applies to
          purchases made after it.
        </p>
      </section>

      <LegalContact topic="No-Refund Policy" />
    </PublicShell>
  );
}
