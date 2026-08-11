export function RecoveryCodes({ codes, onContinue }: { codes: string[]; onContinue: () => void }) {
  function download() {
    const blob = new Blob([
      "CivicLoop recovery codes\nStore offline. Each code works once.\n\n",
      codes.join("\n"),
      "\n",
    ], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "civicloop-recovery-codes.txt";
    anchor.click();
    URL.revokeObjectURL(url);
  }
  return (
    <section className="admin-card" aria-labelledby="recovery-codes-title">
      <p className="admin-eyebrow">One-time display</p>
      <h1 id="recovery-codes-title">Save your recovery codes</h1>
      <div className="admin-warning" role="note"><strong>These codes will not be shown again.</strong> Store them offline and separate from your password.</div>
      <ol className="admin-code-list">{codes.map((code) => <li key={code}><code>{code}</code></li>)}</ol>
      <div className="admin-actions">
        <button className="admin-button admin-button--secondary" onClick={() => window.print()} type="button">Print codes</button>
        <button className="admin-button admin-button--secondary" onClick={download} type="button">Download text file</button>
        <button className="admin-button admin-button--primary" onClick={onContinue} type="button">I saved these codes</button>
      </div>
    </section>
  );
}
