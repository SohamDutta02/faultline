import { Fragment } from "react";

type Json = null | boolean | number | string | Json[] | { [k: string]: Json };

function Value({ value, indent }: { value: Json; indent: number }) {
  const pad = "  ".repeat(indent + 1);
  const close = "  ".repeat(indent);
  if (value === null || typeof value === "boolean") return <span className="json-lit">{String(value)}</span>;
  if (typeof value === "number") return <span className="json-num">{value}</span>;
  if (typeof value === "string") return <span className="json-str">{JSON.stringify(value)}</span>;
  if (Array.isArray(value)) {
    if (!value.length) return <>[]</>;
    return (
      <>
        {"[\n"}
        {value.map((v, i) => (
          <Fragment key={i}>
            {pad}
            <Value value={v} indent={indent + 1} />
            {i < value.length - 1 ? ",\n" : "\n"}
          </Fragment>
        ))}
        {close}]
      </>
    );
  }
  const entries = Object.entries(value);
  if (!entries.length) return <>{"{}"}</>;
  return (
    <>
      {"{\n"}
      {entries.map(([k, v], i) => (
        <Fragment key={k}>
          {pad}
          <span className="json-key">{JSON.stringify(k)}</span>: <Value value={v} indent={indent + 1} />
          {i < entries.length - 1 ? ",\n" : "\n"}
        </Fragment>
      ))}
      {close}
      {"}"}
    </>
  );
}

/** Pretty-prints a response body; shows it raw, flagged, when it isn't JSON. */
export function JsonBody({ text }: { text: string }) {
  let parsed: Json | undefined;
  try {
    parsed = JSON.parse(text) as Json;
  } catch {
    parsed = undefined;
  }
  if (parsed === undefined) {
    return (
      <div className="body raw">
        <div className="label" style={{ marginBottom: 4 }}>
          not valid JSON
        </div>
        <pre>{text || "(empty body)"}</pre>
      </div>
    );
  }
  return (
    <div className="body">
      <pre>
        <Value value={parsed} indent={0} />
      </pre>
    </div>
  );
}
