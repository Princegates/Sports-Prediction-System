import { Link } from "react-router-dom";
import { LegalContact } from "../components/LegalContact";
import { PublicShell } from "../components/PublicShell";
import { LEGAL } from "../lib/legal";

/**
 * Privacy Policy. Every row of the first table is something the backend
 * really stores (checked against the models, the account-deletion code and
 * the assistant's LLM calls), not a generic template. Have it reviewed by a
 * lawyer before relying on it.
 */

const COLLECTED: { what: string; detail: string; why: string }[] = [
  {
    what: "Account details",
    detail: "Your name, email address, date of birth, and a scrambled (hashed) version of your password. We never store your password itself.",
    why: "To create your account, confirm you are 18+, and let you sign in.",
  },
  {
    what: "Access and payments",
    detail:
      "Which access codes you redeemed, when your access starts and ends, and for each payment: its reference, amount, status and date. We do not receive your card number or mobile money PIN.",
    why: "To give you access, answer payment questions, and keep financial records.",
  },
  {
    what: "How you use the service",
    detail:
      "Matches you looked at, your AI Generation runs and settings, slips and selections you saved, and picks you chose. Your referral code and who you referred.",
    why: "To run the features you use, enforce run limits, and show your history.",
  },
  {
    what: "Messages to Guda",
    detail: "What you type to the assistant and its answers, saved with your account.",
    why: "To answer you and keep the conversation going.",
  },
  {
    what: "Winning slips you share",
    detail: "The screenshot or PDF you upload to \"Share a win\". For PDFs we remove your account link and IP address.",
    why: "To show it on the site after our team checks it.",
  },
  {
    what: "Technical and security data",
    detail:
      "Your IP address, which we record on account activity (like signing in) and use to limit abuse. Basic browser and device information. Page-view counts from Cloudflare Web Analytics, which does not use cookies and does not follow you across sites.",
    why: "To keep the service safe, stop fraud and abuse, and see how many people visit.",
  },
  {
    what: "Emails we send",
    detail: "Email confirmation, password resets, access and price notices, and, only if you turn it on, the weekly picks email.",
    why: "To run your account and keep you informed.",
  },
];

export function Privacy() {
  return (
    <PublicShell
      title="Privacy Policy — Socca Intelligence"
      description="What personal information Socca Intelligence collects, why, who it is shared with, how long it is kept, and the choices you have."
    >
      <section className="doc-hero">
        <span className="eyebrow">Legal</span>
        <h1>Privacy Policy</h1>
        <p className="lede">
          This page explains what we do with your personal information. The short version: we collect only
          what we need to run your account, we never sell your data, and there are no ads. Last updated{" "}
          {LEGAL.updated}.
        </p>
      </section>

      <section className="doc-section">
        <h2>Who is responsible for your data</h2>
        <p>
          {LEGAL.operator}, which runs {LEGAL.site}, decides how your personal information is used. We aim
          to follow the data protection laws of {LEGAL.country}. You can contact us at the bottom of this
          page.
        </p>
      </section>

      <section className="doc-section">
        <h2>What we collect, and why</h2>
        <div className="doc-table-wrap">
          <table className="doc-table">
            <thead>
              <tr>
                <th>Information</th>
                <th>What it includes</th>
                <th>Why we need it</th>
              </tr>
            </thead>
            <tbody>
              {COLLECTED.map((row) => (
                <tr key={row.what}>
                  <td>
                    <strong>{row.what}</strong>
                  </td>
                  <td>{row.detail}</td>
                  <td>{row.why}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p>
          We also keep small preferences in your own browser (like light or dark mode). See our{" "}
          <Link to="/cookie-policy">Cookie Policy</Link> for the full list.
        </p>
      </section>

      <section className="doc-section">
        <h2>How we use it</h2>
        <ul className="doc-list">
          <li>To run your account and the features you use.</li>
          <li>To check your age and email address, take payments and give you access.</li>
          <li>To keep the service secure, apply fair-use limits, and prevent fraud and abuse.</li>
          <li>To help you when you contact us.</li>
          <li>To send the emails described above. The weekly picks email is off unless you switch it on in your Profile, and you can turn it off again there.</li>
          <li>To follow the law and keep financial records.</li>
        </ul>
        <p>
          We do this because it is needed to give you the service you asked for, because you agreed to it,
          because it protects the service and its users, or because the law requires it.
        </p>
      </section>

      <section className="doc-section">
        <h2>Who we share it with</h2>
        <p>We do not sell your personal information, and we do not show ads. We use these kinds of companies to run the service:</p>
        <ul className="doc-list">
          <li><strong>Hubtel</strong>, to take payments. They collect your payment details themselves.</li>
          <li><strong>Hosting, database and network providers</strong> (for example Oracle Cloud, Supabase and Cloudflare), which store and deliver the service.</li>
          <li><strong>An email delivery provider</strong>, to send our emails.</li>
          <li>
            <strong>An AI language provider</strong>, only if that optional feature is switched on (currently
            Google's Gemini service). It receives the text of your question or a draft answer to reword. We
            do not send your name, email or account details.
          </li>
          <li><strong>Authorities</strong>, when the law requires it or to protect people from harm or fraud.</li>
        </ul>
        <p>
          Some of these companies handle data in other countries, so your information may be processed
          outside {LEGAL.country}. We expect them to protect it.
        </p>
      </section>

      <section className="doc-section">
        <h2>How long we keep it</h2>
        <ul className="doc-list">
          <li>Your account information, history and messages are kept while your account exists.</li>
          <li>
            When an account is deleted, we remove the account, its access records, chat history, viewing
            history, saved slips and saved selections.
          </li>
          <li>
            Some records stay after deletion but are no longer linked to you: payment records (we must keep
            financial records), and the security log, which keeps what happened but not who it was.
          </li>
          <li>Slips you shared that we published keep their content without your name.</li>
        </ul>
      </section>

      <section className="doc-section">
        <h2>Your choices and rights</h2>
        <ul className="doc-list">
          <li>You can see and change your name, password and email preferences in your Profile.</li>
          <li>You can sign out other devices, and pause your account for a break, from your Profile.</li>
          <li>
            You can ask us for a copy of your information, ask us to correct it, ask us to delete your
            account, or object to how we use it. Contact us and we will normally reply within 30 days.
          </li>
          <li>
            You can complain to the data protection authority in {LEGAL.country} if you think we have handled
            your information wrongly. We would like the chance to fix it first.
          </li>
        </ul>
      </section>

      <section className="doc-section">
        <h2>How we protect it</h2>
        <p>
          Passwords are stored scrambled, the site uses an encrypted (HTTPS) connection, sign-in attempts
          are limited to stop guessing, and only authorised staff can see account information. No system is
          perfectly safe, so please use a strong password that you do not use elsewhere.
        </p>
      </section>

      <section className="doc-section">
        <h2>Children</h2>
        <p>
          The service is for people aged 18 and over. If we learn that someone under 18 has an account, we
          will close it and delete their information.
        </p>
      </section>

      <section className="doc-section">
        <h2>Changes to this policy</h2>
        <p>
          If we change how we use your information, we will update this page and the date at the top. For a
          big change we will also tell you on the site or by email.
        </p>
      </section>

      <LegalContact topic="Privacy Policy" />
    </PublicShell>
  );
}
