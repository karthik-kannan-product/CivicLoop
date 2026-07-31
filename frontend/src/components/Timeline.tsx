import type { DemoState } from "../types";

export function Timeline({ items }: { items: DemoState["timeline"] }) {
  return (
    <section className="timeline-section" aria-labelledby="timeline-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Durable evidence</p>
          <h2 id="timeline-title">Activity timeline</h2>
        </div>
      </div>
      <ol className="timeline">
        {[...items].reverse().map((item) => (
          <li key={item.id}>
            <span className="timeline__marker" aria-hidden="true" />
            <div>
              <strong>{item.action.replaceAll("_", " ")}</strong>
              <span>
                {item.actor} · {item.to_status.replaceAll("_", " ")}
              </span>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

