export function PublicNotFound() {
  return (
    <main className="not-found-page">
      <section className="gateway-panel">
        <p className="eyebrow">Page not found</p>
        <h1>That page is not part of CivicLoop.</h1>
        <p>Use the public homepage or enter the synthetic sandbox.</p>
        <div className="public-actions">
          <a className="button button--primary public-cta" href="/">
            Return home
          </a>
          <a className="public-link" href="/sandbox">
            Explore the sandbox
          </a>
        </div>
      </section>
    </main>
  );
}
