export const eventFactFields = [
  { name: "title", label: "Event title", maxLength: 240 },
  { name: "date", label: "Event date", type: "date" },
  { name: "start_time", label: "Start time", type: "time" },
  { name: "end_time", label: "End time", type: "time" },
  { name: "timezone", label: "Event timezone", maxLength: 64, placeholder: "America/Toronto" },
  { name: "city", label: "City", maxLength: 500 },
  { name: "region", label: "Region or state", maxLength: 500 },
  { name: "country", label: "Country", maxLength: 500 },
  { name: "venue_name", label: "Confirmed venue name", maxLength: 500 },
  { name: "venue_address", label: "Complete venue address", maxLength: 500 },
  { name: "access_instructions", label: "Arrival and accessibility instructions", maxLength: 2000 },
  { name: "signup_url", label: "Signup URL", type: "url", maxLength: 500 },
] as const;

export function publicEventFacts(form: HTMLFormElement): Record<string, string> {
  const data = new FormData(form);
  return Object.fromEntries(eventFactFields.map(({ name }) => [name, String(data.get(name) ?? "").trim()]));
}

export function EventFactsFields({ facts = {}, disabled = false }: {
  facts?: Record<string, string | number | boolean>;
  disabled?: boolean;
}) {
  return (
    <fieldset className="event-facts-fields" disabled={disabled}>
      <legend>Public event facts</legend>
      {eventFactFields.map((field) => (
        <label key={field.name} className={field.name === "access_instructions" ? "event-facts-fields__wide" : undefined}>
          <span>{field.label}</span>
          {field.name === "access_instructions" ? (
            <textarea name={field.name} defaultValue={String(facts[field.name] ?? "")} maxLength={field.maxLength} rows={3} required />
          ) : (
            <input name={field.name} defaultValue={String(facts[field.name] ?? "")}
              type={"type" in field ? field.type : "text"}
              maxLength={"maxLength" in field ? field.maxLength : undefined}
              placeholder={"placeholder" in field ? field.placeholder : undefined} required />
          )}
        </label>
      ))}
    </fieldset>
  );
}
