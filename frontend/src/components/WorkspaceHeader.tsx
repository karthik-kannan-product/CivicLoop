import type { Actor } from "../types";

type SessionUser = {
  display_name: string;
  role: "operator" | "approver";
};

type Props = {
  actors: Actor[];
  actor: string;
  busy: boolean;
  deploymentMode?: "server" | "browser_local";
  onActorChange: (actor: string) => void;
  onReset: () => void;
  sessionUser?: SessionUser;
  onLogout?: () => void;
};

export function WorkspaceHeader({
  actors,
  actor,
  busy,
  deploymentMode,
  onActorChange,
  onReset,
  sessionUser,
  onLogout,
}: Props) {
  const serverWorkspace = deploymentMode === "server" && sessionUser;
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand__mark" aria-hidden="true">
          CL
        </span>
        <div>
          <p className="brand__name">CivicLoop</p>
          <p className="brand__context">
            {deploymentMode === "browser_local"
              ? "LaunchLoop · browser-local simulation"
              : "LaunchLoop · authenticated demo workspace"}
          </p>
        </div>
      </div>
      <div className="topbar__actions">
        {serverWorkspace ? (
          <>
            <div className="session-user">
              <span>{sessionUser.display_name}</span>
              <strong>{sessionUser.role}</strong>
            </div>
            {sessionUser.role === "operator" && (
              <button className="button button--quiet" disabled={busy} onClick={onReset}>
                Reset workspace
              </button>
            )}
            <button className="button button--quiet" disabled={busy} onClick={onLogout}>
              Log out
            </button>
          </>
        ) : (
          <>
            <label className="persona">
              <span>Demo persona</span>
              <select
                aria-label="Demo persona"
                value={actor}
                onChange={(event) => onActorChange(event.target.value)}
              >
                {actors.map((item) => (
                  <option key={item.slug} value={item.slug}>
                    {item.display_name} · {item.role}
                  </option>
                ))}
              </select>
            </label>
            <button className="button button--quiet" disabled={busy} onClick={onReset}>
              Reset demo
            </button>
          </>
        )}
      </div>
    </header>
  );
}