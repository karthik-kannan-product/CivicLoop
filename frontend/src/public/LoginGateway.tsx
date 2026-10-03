export function LoginGateway() {
  return (
    <main className="gateway-page">
      <section className="gateway-panel" aria-labelledby="gateway-title">
        <a className="public-brand" href="/">
          CivicLoop
        </a>
        <p className="eyebrow">Secure entry</p>
        <h1 id="gateway-title">Choose how you want to enter CivicLoop.</h1>
        <p>
          Sandbox and owner identities are separate so demonstration access never
          grants administrative access.
        </p>
        <div className="gateway-grid">
          <article className="gateway-card">
            <h2>Synthetic sandbox</h2>
            <p>Explore the two-role LaunchLoop journey using synthetic data.</p>
            <a
              className="button button--primary public-cta"
              href="/sandbox"
            >
              Enter the sandbox
            </a>
          </article>
          <article className="gateway-card">
            <h2>Owner administration</h2>
            <p>
              Manage security and configured integrations through the MFA-protected
              owner surface.
            </p>
            <a
              className="button public-cta public-cta--secondary"
              href="/admin/security"
            >
              Open owner administration
            </a>
          </article>
        </div>
        <a className="public-link" href="/">
          Return to CivicLoop
        </a>
      </section>
    </main>
  );
}
