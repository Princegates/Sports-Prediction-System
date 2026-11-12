import { Link } from "react-router-dom";
import { LegalContact } from "../components/LegalContact";
import { PublicShell } from "../components/PublicShell";
import { LEGAL } from "../lib/legal";

/**
 * Terms of Service. Written to match what the platform really does (AI
 * Generation runs and extras, access codes, Hubtel payments, Share a win),
 * so a statement here is something the code backs up. Plain English on
 * purpose. Have it reviewed by a lawyer before relying on it.
 */
export function Terms() {
  return (
    <PublicShell
      title="Terms of Service — Socca Intelligence"
      description="The rules for using Socca Intelligence: what the service is and isn't, accounts and access, payments, acceptable use and your responsibilities."
    >
      <section className="doc-hero">
        <span className="eyebrow">Legal</span>
        <h1>Terms of Service</h1>
        <p className="lede">
          These are the rules for using {LEGAL.site}. Please read them. By creating an account or using the
          service, you agree to them. Last updated {LEGAL.updated}.
        </p>
      </section>

      <section className="doc-section">
        <div className="doc-callout warn">
          <h4>The most important points</h4>
          <ul>
            <li>
              <strong>We are not a betting company.</strong> We never take bets and we never hold your
              money for betting.
            </li>
            <li>
              <strong>Predictions are chances, not promises.</strong> Any prediction can be wrong, and you
              can lose money if you bet.
            </li>
            <li>
              <strong>You must be 18 or older.</strong>
            </li>
          </ul>
        </div>
      </section>

      <section className="doc-section">
        <h2>1. Who we are</h2>
        <p>
          {LEGAL.site} is run by {LEGAL.operator} ("we", "us", "our"). In these terms, "you" means anyone who
          uses the website or app.
        </p>
      </section>

      <section className="doc-section">
        <h2>2. What the service is</h2>
        <p>
          We provide football analysis. Our computer models estimate how likely different results are, and
          we show those estimates, the reasons behind them, and slips (groups of picks) built from them.
        </p>
        <ul className="doc-list">
          <li>
            <strong>It is information, not advice.</strong> Nothing on the platform is financial, betting or
            legal advice, and nothing is a recommendation to place a bet.
          </li>
          <li>
            <strong>There are no guarantees.</strong> Our figures are estimates from past data. The more
            matches you combine in one slip, the smaller the chance that every one of them is right.
          </li>
          <li>
            <strong>We do not run betting.</strong> We do not accept bets, hold stakes or pay out winnings.
            Booking codes, if offered, only load picks into a betting site that you use yourself. Those sites
            are separate companies with their own rules, and we are not responsible for them.
          </li>
        </ul>
      </section>

      <section className="doc-section">
        <h2>3. Who can use it</h2>
        <ul className="doc-list">
          <li>You must be at least 18 years old. We ask for your date of birth when you register.</li>
          <li>You must give true information and keep it up to date.</li>
          <li>One person, one account. Keep your password private. You are responsible for what happens on your account.</li>
          <li>Make sure that using a service like this is allowed where you live. That is your responsibility.</li>
        </ul>
      </section>

      <section className="doc-section">
        <h2>4. Accounts, free trial and access</h2>
        <p>
          You can create an account for free. New accounts may get a free trial, which starts when you
          confirm your email address. Full access lasts for a set period, which starts when you redeem an
          access code or when a payment is confirmed. When the period ends, access ends too, unless you
          buy or redeem more.
        </p>
        <p>
          Access codes are for the person they were issued to. Do not share, sell or resell them. We can
          change the trial, prices or what is included in future, and the price you see when you pay is the
          price that applies to that purchase.
        </p>
      </section>

      <section className="doc-section">
        <h2>5. Payments</h2>
        <ul className="doc-list">
          <li>Prices are shown on our <Link to="/pricing">Pricing</Link> page and at checkout, in Ghana cedis (GH₵) unless stated otherwise.</li>
          <li>
            Payments are processed by Hubtel, a separate payment company. We never see or store your card
            number or mobile money PIN.
          </li>
          <li>Every purchase is a one-off payment. Nothing renews automatically.</li>
          <li>Access or extra runs are added once Hubtel confirms the payment.</li>
          <li>
            All sales are final and not refundable, as set out in our <Link to="/refunds">No-Refund Policy</Link>,
            which is part of these terms. If a payment goes wrong we fix your access instead.
          </li>
        </ul>
      </section>

      <section className="doc-section">
        <h2>6. AI Generation runs</h2>
        <p>
          AI Generation has a daily allowance of runs, and every run (including pressing Refresh) uses one.
          You can sometimes buy extras: packs of extra runs, or a 24-hour pass with no limit. Extra runs
          stay on your account until you use them. Runs and passes belong to your account and cannot be
          transferred, sold or exchanged for money. We can change the daily allowance for future use.
        </p>
      </section>

      <section className="doc-section">
        <h2>7. What you must not do</h2>
        <ul className="doc-list">
          <li>Copy, scrape or download our predictions in bulk, or use bots or scripts to collect them.</li>
          <li>Resell, republish or share our predictions, slips or access with other people as a product or service.</li>
          <li>Share your account, or share, sell or buy access codes.</li>
          <li>Get around limits, security or payment checks, or try to break or overload the service.</li>
          <li>Use the service for anything illegal, or pretend to be someone else.</li>
          <li>Post fake, edited or someone else's winning tickets in "Share a win".</li>
        </ul>
        <p>We can suspend or close accounts that break these rules, with no refund.</p>
      </section>

      <section className="doc-section">
        <h2>8. What you share with us</h2>
        <p>
          If you post a winning slip in "Share a win", it must be your own real ticket. You allow us to
          show it on the site, and we check it first. We may crop, hide details on, or refuse any slip. You
          can ask us to remove one at any time.
        </p>
      </section>

      <section className="doc-section">
        <h2>9. Our work belongs to us</h2>
        <p>
          The models, text, designs, name and logo belong to {LEGAL.operator}. You may use the service for
          yourself, but you may not copy it or use it to build a competing product. Team names and match
          data belong to their owners and are used to describe fixtures.
        </p>
      </section>

      <section className="doc-section">
        <h2>10. Things outside our control</h2>
        <p>
          We use data from other providers, and the service depends on the internet, hosting and payment
          companies. Fixtures, lineups and odds can change or be wrong, and the service can have
          interruptions. We work to keep it running and accurate, but we cannot promise it will always be
          available or error-free.
        </p>
      </section>

      <section className="doc-section">
        <h2>11. Our responsibility to you</h2>
        <p>
          The service is provided "as is". To the extent the law allows, we are not responsible for losses
          from bets or other decisions you make using our predictions, or for indirect or lost profits. Our
          total responsibility to you for any claim is limited to the amount you paid us in the 3 months
          before the claim. Nothing here limits any right you have by law that cannot be limited.
        </p>
      </section>

      <section className="doc-section">
        <h2>12. Play responsibly</h2>
        <p>
          Betting can become a problem. Only use money you can afford to lose, and stop if it stops being
          fun. Read our <Link to="/responsible">Responsible use</Link> page for tips and where to get free
          help. You can pause your account from your Profile.
        </p>
      </section>

      <section className="doc-section">
        <h2>13. Ending your account</h2>
        <p>
          You can stop using the service at any time. To have your account deleted, contact us (see our{" "}
          <Link to="/privacy">Privacy Policy</Link> for what is kept). We can suspend or close an account if
          these terms are broken, if we must by law, or to protect other users or the service.
        </p>
      </section>

      <section className="doc-section">
        <h2>14. Changes to these terms</h2>
        <p>
          We may update these terms. The date at the top shows the latest version. If a change matters, we
          will tell you on the site or by email. Using the service after a change means you accept it.
        </p>
      </section>

      <section className="doc-section">
        <h2>15. Law and disputes</h2>
        <p>
          These terms follow the laws of {LEGAL.country}. If you have a problem, please contact us first, and
          we will try to fix it. If that does not work, the courts of {LEGAL.country} can decide it.
        </p>
      </section>

      <LegalContact topic="Terms of Service" />
    </PublicShell>
  );
}
