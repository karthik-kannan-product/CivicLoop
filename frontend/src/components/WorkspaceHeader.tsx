import type { Actor } from "../types";

type Props = {
  actors: Actor[];
  actor: string;
  busy: boolean;
  deploymentMode?: "server" | "browser_local";
  onActorChange: (actor: string) => void;
  onReset: () => void;
};

export function WorkspaceHeader({
  actors,
  actor,
  busy,
  deploymentMode,
  onActorChange,
  onReset,
}: Props) {
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
              : "LaunchLoop demo workspace"}
          </p>
        </div>
      </div>
      <div className="topbar__actions">
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
      </div>
    </header>
  );
}
