"use client";

/**
 * A form generated from a JSON Schema — the shape the API publishes for each
 * of the six import-profile rule columns (`GET /import-profiles/rule-schemas`).
 *
 * Handles what those schemas use: object properties of type boolean, integer,
 * string, enum / const, arrays of strings, and one array of objects
 * (`column_map.columns`). The API is the validator; this form only keeps the
 * value the right shape and lets the server's field errors show.
 */

import type { JsonSchema } from "@/lib/types";

type Value = Record<string, unknown>;

interface PropertySchema {
  type?: string | string[];
  enum?: unknown[];
  const?: unknown;
  default?: unknown;
  title?: string;
  description?: string;
  items?: PropertySchema & { $ref?: string };
  anyOf?: PropertySchema[];
  $ref?: string;
  properties?: Record<string, PropertySchema>;
  required?: string[];
}

function resolve(schema: PropertySchema, root: JsonSchema): PropertySchema {
  if (schema.$ref) {
    const name = schema.$ref.replace("#/$defs/", "");
    const defs = (root.$defs ?? {}) as Record<string, PropertySchema>;
    return defs[name] ?? {};
  }
  return schema;
}

function typeOf(schema: PropertySchema): string {
  if (schema.const !== undefined) return "const";
  if (schema.enum) return "enum";
  if (schema.anyOf) {
    const types = schema.anyOf.map((s) => s.type).filter((t) => t && t !== "null");
    if (types.includes("string") && types.includes("integer")) return "string-or-int";
    if (types.length === 1 && typeof types[0] === "string") return types[0];
  }
  if (Array.isArray(schema.type)) return schema.type.find((t) => t !== "null") ?? "string";
  return schema.type ?? "string";
}

export function defaultsOf(root: JsonSchema): Value {
  const properties = (root.properties ?? {}) as Record<string, PropertySchema>;
  const value: Value = {};
  for (const [key, schema] of Object.entries(properties)) {
    if (schema.default !== undefined) value[key] = schema.default;
    else if (schema.const !== undefined) value[key] = schema.const;
    else if (typeOf(schema) === "array") value[key] = [];
    else if (typeOf(schema) === "boolean") value[key] = false;
  }
  return value;
}

export function SchemaForm({
  schema,
  value,
  onChange,
  name,
}: {
  schema: JsonSchema;
  value: Value;
  onChange: (value: Value) => void;
  name: string;
}) {
  const properties = (schema.properties ?? {}) as Record<string, PropertySchema>;
  const set = (key: string, next: unknown) => onChange({ ...value, [key]: next });

  return (
    <fieldset className="schema-form" data-testid={`schema-form-${name}`}>
      {Object.entries(properties).map(([key, raw]) => {
        const property = resolve(raw, schema);
        const kind = typeOf(property);
        const label = property.title ?? key;
        const current = value[key];
        const id = `${name}-${key}`;

        if (kind === "const") {
          return (
            <div className="field" key={key}>
              <span className="field__label">{label}</span>
              <span className="mono">{String(property.const)}</span>
            </div>
          );
        }
        if (kind === "boolean") {
          return (
            <label className="check" key={key} htmlFor={id}>
              <input id={id} type="checkbox" checked={Boolean(current)} onChange={(e) => set(key, e.target.checked)} />
              {label}
            </label>
          );
        }
        if (kind === "enum") {
          return (
            <label className="field" key={key} htmlFor={id}>
              <span className="field__label">{label}</span>
              <select id={id} value={String(current ?? "")} onChange={(e) => set(key, e.target.value)}>
                {(property.enum ?? []).map((option) => (
                  <option key={String(option)} value={String(option)}>
                    {String(option) === "" ? "(none)" : String(option)}
                  </option>
                ))}
              </select>
            </label>
          );
        }
        if (kind === "integer" || kind === "number") {
          return (
            <label className="field" key={key} htmlFor={id}>
              <span className="field__label">{label}</span>
              <input
                id={id}
                type="number"
                value={current === null || current === undefined ? "" : String(current)}
                onChange={(e) => set(key, e.target.value === "" ? null : Number(e.target.value))}
              />
            </label>
          );
        }
        if (kind === "array") {
          const items = property.items ? resolve(property.items, schema) : {};
          if (items.properties) {
            return (
              <ObjectArrayEditor
                key={key}
                label={label}
                itemSchema={items}
                root={schema}
                value={Array.isArray(current) ? (current as Value[]) : []}
                onChange={(next) => set(key, next)}
                name={id}
              />
            );
          }
          return (
            <label className="field" key={key} htmlFor={id}>
              <span className="field__label">{label}</span>
              <input
                id={id}
                value={Array.isArray(current) ? (current as string[]).join(", ") : ""}
                onChange={(e) =>
                  set(
                    key,
                    e.target.value
                      .split(",")
                      .map((v) => v.trim())
                      .filter((v) => v.length > 0),
                  )
                }
                placeholder="comma-separated"
              />
            </label>
          );
        }
        return (
          <label className="field" key={key} htmlFor={id}>
            <span className="field__label">{label}</span>
            <input
              id={id}
              value={current === null || current === undefined ? "" : String(current)}
              onChange={(e) => set(key, e.target.value === "" ? null : e.target.value)}
            />
            {property.description ? <span className="field__hint">{property.description}</span> : null}
          </label>
        );
      })}
    </fieldset>
  );
}

function ObjectArrayEditor({
  label,
  itemSchema,
  root,
  value,
  onChange,
  name,
}: {
  label: string;
  itemSchema: PropertySchema;
  root: JsonSchema;
  value: Value[];
  onChange: (value: Value[]) => void;
  name: string;
}) {
  const properties = itemSchema.properties ?? {};
  const columns = Object.entries(properties);

  const blank = (): Value => {
    const row: Value = {};
    for (const [key, raw] of columns) {
      const property = resolve(raw, root);
      if (property.default !== undefined) row[key] = property.default;
      else if (typeOf(property) === "enum") row[key] = String(property.enum?.[0] ?? "");
      else if (typeOf(property) === "boolean") row[key] = false;
      else row[key] = "";
    }
    return row;
  };

  const update = (index: number, key: string, next: unknown) =>
    onChange(value.map((row, i) => (i === index ? { ...row, [key]: next } : row)));

  return (
    <div className="field">
      <span className="field__label">{label}</span>
      <table className="table table--compact" data-testid={`${name}-table`}>
        <thead>
          <tr>
            {columns.map(([key, raw]) => (
              <th key={key}>{resolve(raw, root).title ?? key}</th>
            ))}
            <th />
          </tr>
        </thead>
        <tbody>
          {value.map((row, index) => (
            <tr key={index}>
              {columns.map(([key, raw]) => {
                const property = resolve(raw, root);
                const kind = typeOf(property);
                const cell = row[key];
                if (kind === "enum") {
                  return (
                    <td key={key}>
                      <select value={String(cell ?? "")} onChange={(e) => update(index, key, e.target.value)} aria-label={key}>
                        {(property.enum ?? []).map((option) => (
                          <option key={String(option)} value={String(option)}>
                            {String(option)}
                          </option>
                        ))}
                      </select>
                    </td>
                  );
                }
                if (kind === "boolean") {
                  return (
                    <td key={key}>
                      <input type="checkbox" checked={Boolean(cell)} onChange={(e) => update(index, key, e.target.checked)} aria-label={key} />
                    </td>
                  );
                }
                return (
                  <td key={key}>
                    <input
                      value={cell === null || cell === undefined ? "" : String(cell)}
                      onChange={(e) => {
                        const text = e.target.value;
                        // A whole number is a 0-based column index; anything else is a header.
                        update(index, key, kind === "string-or-int" && /^\d+$/.test(text) ? Number(text) : text);
                      }}
                      aria-label={key}
                    />
                  </td>
                );
              })}
              <td>
                <button type="button" className="icon-button" aria-label="Remove row" onClick={() => onChange(value.filter((_, i) => i !== index))}>
                  ×
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button type="button" className="button button--ghost" onClick={() => onChange([...value, blank()])}>
        Add column
      </button>
    </div>
  );
}
