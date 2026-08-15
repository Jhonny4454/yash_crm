import { useMemo, useState } from "react";
import { put } from "../api/client";
import { useFetch } from "../api/useFetch";
import MoneyInput from "../components/MoneyInput";
import { Empty, ErrorNote, Loading, readableError } from "../components/ui";
import WhatsAppTester from "../components/WhatsAppTester";
import "../styles/Settings.css";

/**
 * Settings.
 *
 * Every one of these rows is a string in a two-column table, and the screen
 * this replaces showed them exactly that way: 47 identical text boxes with
 * labels generated from the column name. "Tax Type" was a text field you
 * could type "Exclde" into. "Wa Enabled" was a box you typed 1 or 0 into.
 * "Coll Amount Change" told you nothing about what it changed. Nothing
 * validated, so a typo was accepted, saved, and only discovered later when
 * billing or WhatsApp quietly did the wrong thing.
 *
 * The server now describes each key - control, options, range, help text -
 * and this renders what it is told. The dropdowns here and the validation on
 * the save are generated from the same list, so the screen cannot offer a
 * value the API will refuse.
 */

const TRUE_WORDS = new Set(["1", "true", "yes", "on", "enable", "enabled"]);

const asBool = (value) => TRUE_WORDS.has(String(value ?? "").trim().toLowerCase());

/** Client-side mirror of settings_schema.coerce, so mistakes are caught before a round trip. */
function fieldError(field, value) {
  const text = String(value ?? "").trim();
  const label = field.label;

  if (field.input === "select") {
    const allowed = (field.options || []).map((o) => o.value);
    if (allowed.length && !allowed.includes(text)) return `Choose a value for ${label}.`;
    return null;
  }
  if (field.input === "number") {
    if (text === "") return `${label} cannot be blank.`;
    if (!/^-?\d+(\.\d+)?$/.test(text)) return `${label} must be a whole number.`;
    const n = Number(text);
    if (field.min != null && n < field.min) return `${label} cannot be less than ${field.min}.`;
    if (field.max != null && n > field.max) return `${label} cannot be more than ${field.max}.`;
    return null;
  }
  if (field.input === "email" && text && !text.includes("@")) {
    return `${label} does not look like an email address.`;
  }
  if (field.input === "url" && text && !/^https?:\/\//i.test(text)) {
    return `${label} must start with http:// or https://.`;
  }
  if (field.maxlength && text.length > field.maxlength) {
    return `${label} cannot be longer than ${field.maxlength} characters.`;
  }
  return null;
}

export default function SettingsPage() {
  const { data, extra, loading, error, refetch } = useFetch("/settings");
  const [edits, setEdits] = useState({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const [savedNote, setSavedNote] = useState(null);
  const [revealed, setRevealed] = useState({});
  const [saveCount, setSaveCount] = useState(0);

  const fields = useMemo(() => (Array.isArray(data) ? data : []), [data]);
  const original = useMemo(
    () => Object.fromEntries(fields.map((f) => [f.key, f.value ?? ""])),
    [fields],
  );

  const valueOf = (field) => edits[field.key] ?? field.value ?? "";

  // A secret is never sent to the browser, so "unchanged" for one of those
  // means "the box is still empty", not "it matches what is stored".
  const changedKeys = useMemo(
    () => Object.keys(edits).filter((key) => {
      const field = fields.find((f) => f.key === key);
      if (!field) return false;
      if (field.is_secret) return String(edits[key] ?? "") !== "";
      return String(edits[key] ?? "") !== String(original[key] ?? "");
    }),
    [edits, fields, original],
  );

  const errors = useMemo(() => {
    const found = {};
    for (const key of changedKeys) {
      const field = fields.find((f) => f.key === key);
      const message = field && fieldError(field, edits[key]);
      if (message) found[key] = message;
    }
    return found;
  }, [changedKeys, edits, fields]);

  const errorCount = Object.keys(errors).length;

  const groups = useMemo(() => {
    const declared = extra?.groups || [];
    const byKey = new Map();
    for (const field of fields) {
      if (!byKey.has(field.group)) {
        byKey.set(field.group, {
          key: field.group,
          label: field.group_label || field.group,
          hint: field.group_hint || "",
          order: field.group_order ?? 99,
          fields: [],
        });
      }
      byKey.get(field.group).fields.push(field);
    }
    for (const group of declared) {
      const existing = byKey.get(group.key);
      if (existing) {
        existing.label = group.label || existing.label;
        existing.hint = group.hint ?? existing.hint;
      }
    }
    return [...byKey.values()].sort((a, b) => a.order - b.order);
  }, [fields, extra]);

  function set(key, value) {
    setSavedNote(null);
    setSaveError(null);
    setEdits((current) => ({ ...current, [key]: value }));
  }

  function discard() {
    setEdits({});
    setSaveError(null);
    setSavedNote(null);
  }

  async function save(event) {
    event.preventDefault();
    if (!changedKeys.length || errorCount) return;
    setSaving(true);
    setSaveError(null);
    setSavedNote(null);
    try {
      const response = await put("/settings", {
        settings: changedKeys.map((key) => ({ key, value: edits[key] })),
      });
      const payload = response?.data || response;
      setEdits({});
      setRevealed({});
      setSaveCount((n) => n + 1);
      setSavedNote(
        `${payload?.count ?? changedKeys.length} setting${
          (payload?.count ?? changedKeys.length) === 1 ? "" : "s"
        } saved.`,
      );
      refetch();
    } catch (err) {
      setSaveError(err);
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <section className="page"><Loading label="Loading settings" /></section>;
  if (error) {
    return (
      <section className="page">
        <div className="page-heading"><div><h1>Settings</h1></div></div>
        <ErrorNote error={error} onRetry={refetch} />
      </section>
    );
  }

  return (
    <section className="page set-page">
      <div className="page-heading">
        <div>
          <h1>Settings</h1>
          <p>Billing, numbering, messaging and payment preferences.</p>
        </div>
      </div>

      {!fields.length ? (
        <Empty title="No settings available"
               hint="The settings list could not be loaded. Try refreshing." />
      ) : (
        <form onSubmit={save}>
          {savedNote && <div className="alert success set-alert">{savedNote}</div>}
          {saveError && (
            <div className="alert error set-alert">
              <strong>Nothing was saved.</strong>
              {Array.isArray(saveError.errors) && saveError.errors.length ? (
                <ul>{saveError.errors.map((message) => <li key={message}>{message}</li>)}</ul>
              ) : (
                <p>{saveError.detail || readableError(saveError)}</p>
              )}
            </div>
          )}

          {groups.map((group) => (
            <section className="panel set-group" key={group.key}>
              <header className="set-group-head">
                <h2>{group.label}</h2>
                {group.hint && <p>{group.hint}</p>}
              </header>
              <div className="set-fields">
                {[...group.fields]
                  .sort((a, b) => (a.order ?? 999) - (b.order ?? 999)
                    || a.key.localeCompare(b.key))
                  .map((field) => (
                    <SettingRow key={field.key} field={field} value={valueOf(field)}
                                error={errors[field.key]}
                                dirty={changedKeys.includes(field.key)}
                                revealed={!!revealed[field.key]}
                                onReveal={() => setRevealed((r) => ({ ...r, [field.key]: !r[field.key] }))}
                                onChange={(next) => set(field.key, next)} />
                  ))}
              </div>
            </section>
          ))}

          <div className="set-savebar" role="status">
            <span>
              {errorCount
                ? `${errorCount} field${errorCount === 1 ? "" : "s"} need fixing`
                : changedKeys.length
                  ? `${changedKeys.length} unsaved change${changedKeys.length === 1 ? "" : "s"}`
                  : "No changes"}
            </span>
            <div className="set-savebar-actions">
              <button type="button" className="btn" onClick={discard}
                      disabled={!changedKeys.length || saving}>
                Discard
              </button>
              <button className="btn primary" disabled={saving || !changedKeys.length || errorCount > 0}>
                {saving ? "Saving…" : "Save changes"}
              </button>
            </div>
          </div>
        </form>
      )}

      {/* Outside the form on purpose: pressing Enter in the test field must not
          submit the settings form, and sending a test is not "saving".

          Keyed on the save counter so it remounts whenever settings change.
          The tester carries its own copy of gateway, country code and API key,
          seeded once when it loads - so after a save up here it was holding
          the OLD values, and pressing Save down there wrote them straight back
          over the new ones. Two forms on one screen editing the same four keys
          is exactly how a setting appears to revert. */}
      <WhatsAppTester key={saveCount} />
    </section>
  );
}

function SettingRow({ field, value, error, dirty, revealed, onReveal, onChange }) {
  const id = `set-${field.key}`;
  const describedBy = [field.help ? `${id}-help` : null, error ? `${id}-err` : null]
    .filter(Boolean).join(" ") || undefined;

  if (field.input === "switch") {
    const on = asBool(value);
    return (
      <div className={`set-row set-row-switch${dirty ? " is-dirty" : ""}`}>
        <div className="set-row-text">
          <label htmlFor={id}>{field.label}</label>
          {field.help && <p className="set-help" id={`${id}-help`}>{field.help}</p>}
        </div>
        <label className="set-switch">
          <input id={id} type="checkbox" checked={on} aria-describedby={describedBy}
                 onChange={(event) => onChange(event.target.checked ? "True" : "False")} />
          <span className="set-switch-track" aria-hidden="true"><span /></span>
          <span className="set-switch-state">{on ? "On" : "Off"}</span>
        </label>
      </div>
    );
  }

  return (
    <div className={`set-row${dirty ? " is-dirty" : ""}${field.input === "textarea" ? " set-row-wide" : ""}`}>
      <div className="set-row-text">
        <label htmlFor={id}>{field.label}</label>
        {field.help && <p className="set-help" id={`${id}-help`}>{field.help}</p>}
      </div>

      <div className="set-control">
        {field.input === "select" ? (
          <select id={id} className="input" value={value} aria-describedby={describedBy}
                  onChange={(event) => onChange(event.target.value)}>
            {!(field.options || []).some((o) => o.value === String(value)) && (
              <option value={value}>{value ? `${value} (not a known value)` : "Select…"}</option>
            )}
            {(field.options || []).map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        ) : field.input === "textarea" ? (
          <textarea id={id} className={`input${field.mono ? " set-mono" : ""}`}
                    rows={field.rows || 3} value={value} aria-describedby={describedBy}
                    onChange={(event) => onChange(event.target.value)} />
        ) : field.input === "password" ? (
          <div className="set-secret">
            <input id={id} className="input" type={revealed ? "text" : "password"}
                   value={value} autoComplete="new-password" aria-describedby={describedBy}
                   placeholder={field.has_value
                     ? "Saved — leave blank to keep it"
                     : "Not set"}
                   onChange={(event) => onChange(event.target.value)} />
            <button type="button" className="btn sm" onClick={onReveal}
                    aria-label={revealed ? "Hide value" : "Show what you typed"}>
              {revealed ? "Hide" : "Show"}
            </button>
          </div>
        ) : (
          <div className={field.suffix ? "set-suffixed" : undefined}>
            {/* Number settings go through MoneyInput rather than
                `type="number"`. No spinner, and no decimal point - which is
                not a new restriction, only a visible one: settings_schema.py
                already coerces every number field with `int(float(text))`, so
                typing 2.5 here was silently stored as 2 with nothing on screen
                to say so. */}
            {field.input === "number" ? (
              <MoneyInput id={id} className="input" aria-describedby={describedBy}
                          max={field.max}
                          placeholder={field.placeholder || ""} value={value}
                          onChange={(event) => onChange(event.target.value)} />
            ) : (
              <input id={id} className="input" aria-describedby={describedBy}
                     type={field.input === "email" ? "email"
                       : field.input === "url" ? "url" : "text"}
                     maxLength={field.maxlength}
                     placeholder={field.placeholder || ""} value={value}
                     onChange={(event) => onChange(event.target.value)} />
            )}
            {field.suffix && <span className="set-suffix">{field.suffix}</span>}
          </div>
        )}

        {error && <p className="set-error" id={`${id}-err`} role="alert">{error}</p>}
        {!field.known && !error && (
          <p className="set-help set-unknown">
            Stored in this database but not described by the application.
          </p>
        )}
      </div>
    </div>
  );
}
