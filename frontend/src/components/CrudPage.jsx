import { useEffect, useMemo, useState } from "react";
import { del, post, put } from "../api/client";
import { useDebounced, useFetch } from "../api/useFetch";
import { invalidateLookup, useLookup } from "../api/useLookup";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";
import MoneyInput from "./MoneyInput";
import {
  Empty, ErrorNote, Loading, Pager, TableSkeleton, fmtDate, inr, readableError,
} from "./ui";

/**
 * One screen that serves every master-data table.
 *
 * The Jinja2 app had a separate list.html + form.html for each of these.
 * They differ only in columns and labels, so those become props and the
 * behaviour (search, sort, paginate, add, edit, delete, validation, error
 * handling) is written once here.
 *
 * <CrudPage
 *    endpoint="/masters/zones"
 *    title="Zones"
 *    singular="Zone"
 *    columns={[
 *      { key: "name",  label: "Name", required: true },
 *      { key: "value", label: "Rate", type: "number", suffix: "%" },
 *      { key: "is_active", label: "Active", type: "checkbox" },
 *      // A foreign key renders as a dropdown and shows the name, not the id:
 *      { key: "vendor_id", label: "Vendor", type: "lookup",
 *        lookup: "/inventory/vendors", required: true },
 *    ]}
 * />
 */
export default function CrudPage({
  endpoint,
  title,
  singular,
  columns,
  hint,
  canDelete = true,
}) {
  const { isAdmin } = useAuth();
  const { toast, confirm } = useToast();

  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState({ key: null, dir: "asc" });
  const [editing, setEditing] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [selected, setSelected] = useState(() => new Set());

  const q = useDebounced(search);
  const { data, meta, loading, refreshing, error, refetch } = useFetch(endpoint, { q, page });

  const tableCols = columns.filter((c) => !c.hideInTable);
  const blank = Object.fromEntries(columns.map((c) => [
    c.key,
    // A permission field holds a LIST. Seeded as "" it would post an empty
    // string, which the server parses as "no capabilities" rather than as
    // "this field was not touched" - so a new staff member would be created
    // already restricted out of everything.
    c.type === "permissions" ? [] : c.type === "checkbox" ? true : "",
  ]));

  // Sort the current page client-side. Server-side ordering would need an
  // API change; this at least makes each page scannable.
  const rows = useMemo(() => {
    if (!Array.isArray(data)) return [];
    if (!sort.key) return data;
    const factor = sort.dir === "asc" ? 1 : -1;
    return [...data].sort((a, b) => {
      const left = a[sort.key];
      const right = b[sort.key];
      if (left == null && right == null) return 0;
      if (left == null) return 1;   // blanks always sink
      if (right == null) return -1;
      if (typeof left === "number" && typeof right === "number") {
        return (left - right) * factor;
      }
      return String(left).localeCompare(String(right), undefined, { numeric: true }) * factor;
    });
  }, [data, sort]);

  function toggleSort(key) {
    setSort((prev) =>
      prev.key === key
        ? { key, dir: prev.dir === "asc" ? "desc" : "asc" }
        : { key, dir: "asc" });
  }

  async function remove(row) {
    const name = row.name || row.bill_no || `#${row.id}`;
    const okToDelete = await confirm({
      title: `Delete ${singular.toLowerCase()}?`,
      message: `“${name}” will be permanently removed. This cannot be undone.`,
      confirmLabel: "Delete",
      tone: "danger",
    });
    if (!okToDelete) return;

    setBusyId(row.id);
    setActionError(null);
    try {
      await del(`${endpoint}/${row.id}`);
      invalidateLookup(endpoint); // any dropdown fed by this table is now stale
      toast.success(`${singular} deleted.`);
      refetch();
    } catch (err) {
      // The API answers 409 in_use when another record still points at this row.
      if (err.message === "in_use") {
        toast.error(`This ${singular.toLowerCase()} is still in use elsewhere and cannot be deleted.`);
      } else {
        setActionError(err);
        toast.error(readableError(err));
      }
    } finally {
      setBusyId(null);
    }
  }


  const allSelected = rows.length > 0 && rows.every((r) => selected.has(r.id));

  // A different search or a different page is a DIFFERENT list. The old code
  // kept every tick forever, so a selection from page 3 silently survived onto
  // page 1 - and the select-all checkbox, which decided "all" by comparing
  // sizes, would clear three invisible rows when the page happened to hold
  // three rows of its own. Clearing on a list change also means "Delete
  // selected" can only ever touch rows the operator can see.
  useEffect(() => { setSelected(new Set()); }, [q, page]);

  function toggleRow(id) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    // Decided against the CURRENT page, not against the size of the whole
    // selection - which is what made the old version clear the wrong rows
    // whenever a previous page's picks made the sizes match by accident.
    setSelected((prev) => {
      const next = new Set(prev);
      if (allSelected) rows.forEach((r) => next.delete(r.id));
      else rows.forEach((r) => next.add(r.id));
      return next;
    });
  }

  async function removeSelected() {
    const ids = [...selected];
    const confirmed = await confirm({
      title: `Delete ${ids.length} ${ids.length === 1 ? singular.toLowerCase() : title.toLowerCase()}?`,
      message: "These records will be permanently removed. This cannot be undone.",
      confirmLabel: `Delete ${ids.length}`,
      tone: "danger",
    });
    if (!confirmed) return;

    setBusyId("bulk");
    // Sequential rather than parallel: the API rejects rows that are still
    // referenced, and we want to report exactly which ones survived.
    let done = 0;
    const failed = [];
    for (const id of ids) {
      try {
        await del(`${endpoint}/${id}`);
        done += 1;
      } catch {
        failed.push(id);
      }
    }
    setBusyId(null);
    setSelected(new Set());
    invalidateLookup(endpoint);

    if (done) toast.success(`${done} ${done === 1 ? "record" : "records"} deleted.`);
    if (failed.length) {
      toast.error(`${failed.length} could not be deleted — they are still in use elsewhere.`);
    }
    refetch();
  }

  function exportCsv() {
    const cols = tableCols;
    const escape = (v) => {
      const text = v === null || v === undefined ? "" : String(v);
      return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
    };
    const source = selected.size ? rows.filter((r) => selected.has(r.id)) : rows;
    const lines = [cols.map((c) => escape(c.label)).join(",")];
    for (const row of source) {
      lines.push(cols.map((c) => escape(row[c.key])).join(","));
    }
    const blob = new Blob(["\ufeff" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const href = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = href;
    a.download = `${title.toLowerCase().replace(/\s+/g, "-")}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(href);
    toast.info(`Exported ${source.length} ${source.length === 1 ? "row" : "rows"}.`);
  }

  return (
    <>
      <div className="toolbar">
        <input
          className="input grow"
          type="search"
          placeholder={`Search ${title.toLowerCase()}`}
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1); }}
          aria-label={`Search ${title.toLowerCase()}`}
        />
        <button className="btn" onClick={exportCsv} disabled={!rows.length}
                title="Download the rows shown below as CSV">
          <i className="fas fa-download" aria-hidden="true" /> Export
        </button>
        <button className="btn primary" onClick={() => setEditing({ ...blank })}>
          <i className="fas fa-plus" aria-hidden="true" /> Add {singular}
        </button>
      </div>

      {selected.size > 0 && (
        <div className="bulk-bar" role="status">
          <span><strong>{selected.size}</strong> selected</span>
          <div className="bulk-actions">
            <button className="btn sm" onClick={() => setSelected(new Set())}>Clear</button>
            <button className="btn sm" onClick={exportCsv}>Export selected</button>
            {canDelete && isAdmin && (
              <button className="btn sm danger" disabled={busyId === "bulk"} onClick={removeSelected}>
                {busyId === "bulk" ? "Deleting…" : "Delete selected"}
              </button>
            )}
          </div>
        </div>
      )}

      {hint && <div className="hint" style={{ marginBottom: 12 }}>{hint}</div>}

      <ErrorNote error={error} onRetry={refetch} />
      {actionError && <div className="alert error">{readableError(actionError)}</div>}

      <div className="card">
        {/* `refreshing` dims the existing rows; `loading` means there are no
            rows yet. Searching and paging now land in the first case, so the
            table stays on screen instead of being replaced by a spinner. */}
        <div className={`table-wrap${refreshing ? " is-refreshing" : ""}`}>
          {loading ? (
            <TableSkeleton rows={6} cols={tableCols.length + 2}
              label={`Loading ${title.toLowerCase()}`} />
          ) : !rows.length ? (
            <Empty
              title={search ? "Nothing matches" : `No ${title.toLowerCase()} yet`}
              hint={search ? "Try a different search." : `Add your first ${singular.toLowerCase()}.`}
              action={!search && (
                <button className="btn primary" onClick={() => setEditing({ ...blank })}>
                  Add {singular}
                </button>
              )}
            />
          ) : (
            /* `cards-sm` + the data-label on every cell is what lets this
               turn into a stack of labelled cards below 720px. A master table
               with seven columns cannot be read on a phone however far you
               let it scroll sideways. */
            <table className="data cards-sm">
              <thead>
                <tr>
                  <th className="select-col">
                    <input
                      type="checkbox"
                      checked={allSelected}
                      ref={(el) => {
                        if (el) el.indeterminate = selected.size > 0 && !allSelected;
                      }}
                      onChange={toggleAll}
                      aria-label={allSelected ? "Clear selection" : "Select all rows on this page"}
                    />
                  </th>
                  {tableCols.map((c) => (
                    <th
                      key={c.key}
                      className={`${c.type === "number" || c.type === "money" ? "right" : ""} sortable`}
                      aria-sort={sort.key === c.key ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
                    >
                      <button type="button" onClick={() => toggleSort(c.key)}>
                        {c.label}
                        <span className="sort-caret" aria-hidden="true">
                          {sort.key === c.key ? (sort.dir === "asc" ? "▲" : "▼") : "⇅"}
                        </span>
                      </button>
                    </th>
                  ))}
                  <th className="right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id} className={selected.has(row.id) ? "is-selected" : undefined}>
                    <td className="select-col" data-label="Select">
                      <input
                        type="checkbox"
                        checked={selected.has(row.id)}
                        onChange={() => toggleRow(row.id)}
                        aria-label={`Select ${row.name || `record ${row.id}`}`}
                      />
                    </td>
                    {tableCols.map((c) => (
                      <td
                        key={c.key}
                        data-label={c.label}
                        className={c.type === "number" || c.type === "money" ? "right num" : ""}
                      >
                        {c.type === "lookup"
                          ? <LookupCell column={c} value={row[c.key]} />
                          : renderCell(row[c.key], c)}
                      </td>
                    ))}
                    <td className="right row-actions" data-label="Actions">
                      <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
                        <button className="btn sm" onClick={() => setEditing({ ...row })}>
                          Edit
                        </button>
                        {canDelete && isAdmin && (
                          <button
                            className="btn sm danger"
                            disabled={busyId === row.id}
                            onClick={() => remove(row)}
                          >
                            {busyId === row.id ? "Deleting…" : "Delete"}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <Pager meta={meta} onPage={setPage} />
      </div>

      {editing && (
        <CrudDialog
          endpoint={endpoint}
          singular={singular}
          columns={columns}
          value={editing}
          onClose={() => setEditing(null)}
          onSaved={(wasNew) => {
            setEditing(null);
            invalidateLookup(endpoint);
            toast.success(wasNew ? `${singular} added.` : `${singular} updated.`);
            refetch();
          }}
        />
      )}
    </>
  );
}

/* ------------------------------------------------------------------ */

/** Renders a foreign key as its human name rather than a bare number. */
function LookupCell({ column, value }) {
  const { byValue, loading } = useLookup(column.lookup, {
    valueKey: column.valueKey || "id",
    labelKey: column.labelKey || "name",
  });
  if (value === null || value === undefined || value === "") return "—";
  if (loading) return <span className="muted">…</span>;
  return byValue.get(String(value)) || `#${value}`;
}

function renderCell(value, col) {
  if (col.type === "checkbox") {
    return <span className={`pill ${value ? "ok" : "idle"}`}>{value ? "yes" : "no"}</span>;
  }
  if (value === null || value === undefined || value === "") return "—";
  if (col.type === "money") return inr(value);
  if (col.type === "date") return fmtDate(value);
  if (col.type === "number") return `${value}${col.suffix || ""}`;
  return String(value);
}

/* ------------------------------------------------------------------ */

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

function validateForm(columns, form) {
  const errors = {};
  for (const c of columns) {
    const value = form[c.key];
    const isBlank = value === "" || value === null || value === undefined;

    if (c.required && c.type !== "checkbox" && isBlank) {
      errors[c.key] = `${c.label} is required.`;
      continue;
    }
    if (isBlank) continue;

    if (c.type === "email" && !EMAIL_RE.test(String(value))) {
      errors[c.key] = "Enter a valid email address.";
    }
    if ((c.type === "number" || c.type === "money") && Number.isNaN(Number(value))) {
      errors[c.key] = "Enter a number.";
    }
    if (c.type === "money" && Number(value) < 0) {
      errors[c.key] = "Amount cannot be negative.";
    }
    if (c.min !== undefined && Number(value) < c.min) {
      errors[c.key] = `Must be at least ${c.min}.`;
    }
    if (c.max !== undefined && Number(value) > c.max) {
      errors[c.key] = `Must be at most ${c.max}.`;
    }
  }
  return errors;
}

function CrudDialog({ endpoint, singular, columns, value, onClose, onSaved }) {
  const { toast } = useToast();
  const [form, setForm] = useState(value);
  const [touched, setTouched] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const isNew = !form.id;

  const errors = useMemo(() => validateForm(columns, form), [columns, form]);
  const isValid = Object.keys(errors).length === 0;

  const set = (key, type) => (e) => {
    const v = type === "checkbox" ? e.target.checked : e.target.value;
    setForm((f) => ({ ...f, [key]: v }));
  };
  const blur = (key) => () => setTouched((t) => ({ ...t, [key]: true }));
  const errorFor = (key) => (touched[key] ? errors[key] : undefined);

  async function save(e) {
    e.preventDefault();
    setTouched(Object.fromEntries(columns.map((c) => [c.key, true])));
    if (!isValid || busy) return; // duplicate-submit guard

    setBusy(true);
    setError(null);
    try {
      if (isNew) await post(endpoint, form);
      else await put(`${endpoint}/${form.id}`, form);
      onSaved(isNew);
    } catch (err) {
      if (err.message === "duplicate_or_invalid") {
        toast.error(`A ${singular.toLowerCase()} with those details already exists.`);
      }
      setError(err);
      setBusy(false);
    }
  }

  const wide = columns.length > 5;

  return (
    <div className="modal-scrim" onClick={onClose}>
      <form
        className="card modal-card"
        style={{ maxWidth: wide ? 640 : 460 }}
        onClick={(e) => e.stopPropagation()}
        onSubmit={save}
        noValidate
      >
        <div className="card-head">
          <h2>{isNew ? `Add ${singular}` : `Edit ${form.name || singular}`}</h2>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">✕</button>
        </div>

        <div className="card-body">
          {error && <div className="alert error">{readableError(error)}</div>}

          <div className={wide ? "grid grid-2" : ""}>
            {columns.map((c) => {
              const fieldError = errorFor(c.key);

              if (c.type === "checkbox") {
                return (
                  <label key={c.key} className="check-row">
                    <input type="checkbox" checked={!!form[c.key]} onChange={set(c.key, "checkbox")} />
                    {c.label}
                  </label>
                );
              }

              if (c.type === "permissions") {
                return (
                  <PermissionsField
                    key={c.key}
                    label={c.label}
                    value={form[c.key]}
                    role={form.role}
                    onChange={(next) => setForm((f) => ({ ...f, [c.key]: next }))}
                    wide={wide}
                  />
                );
              }

              if (c.type === "lookup") {
                return (
                  <LookupField
                    key={c.key}
                    column={c}
                    value={form[c.key] ?? ""}
                    onChange={set(c.key)}
                    onBlur={blur(c.key)}
                    error={fieldError}
                    wide={wide}
                  />
                );
              }

              if (c.type === "textarea") {
                return (
                  <div className={`field${fieldError ? " has-error" : ""}`} key={c.key}
                       style={wide ? { gridColumn: "1 / -1" } : {}}>
                    <label>{c.label}{c.required && " *"}</label>
                    <textarea className="input" rows={2} value={form[c.key] ?? ""}
                              onChange={set(c.key)} onBlur={blur(c.key)} />
                    {fieldError && <div className="field-error">{fieldError}</div>}
                  </div>
                );
              }

              if (c.options) {
                return (
                  <div className={`field${fieldError ? " has-error" : ""}`} key={c.key}>
                    <label>{c.label}{c.required && " *"}</label>
                    <select className="input" value={form[c.key] ?? ""}
                            onChange={set(c.key)} onBlur={blur(c.key)}>
                      <option value="">Select…</option>
                      {c.options.map((option) => (
                        <option key={option} value={option}>{option}</option>
                      ))}
                    </select>
                    {fieldError && <div className="field-error">{fieldError}</div>}
                  </div>
                );
              }

              if (c.type === "money" || c.type === "number") {
                return (
                  <div className={`field${fieldError ? " has-error" : ""}`} key={c.key}>
                    <label>{c.label}{c.required && " *"}</label>
                    <MoneyInput
                      className="input"
                      value={form[c.key] ?? ""}
                      max={c.max}
                      placeholder={c.placeholder}
                      required={c.required}
                      onChange={set(c.key)}
                      onBlur={blur(c.key)}
                      aria-invalid={Boolean(fieldError)}
                      autoFocus={c.key === "name"}
                    />
                    {fieldError
                      ? <div className="field-error">{fieldError}</div>
                      : c.suffix && <div className="hint">Enter a whole number. Suffix “{c.suffix}” is added automatically.</div>}
                  </div>
                );
              }

              return (
                <div className={`field${fieldError ? " has-error" : ""}`} key={c.key}>
                  <label>{c.label}{c.required && " *"}</label>
                  <input
                    className="input"
                    type={inputType(c.type)}
                    inputMode={c.type === "tel" ? "numeric" : undefined}
                    value={form[c.key] ?? ""}
                    onChange={set(c.key)}
                    onBlur={blur(c.key)}
                    aria-invalid={Boolean(fieldError)}
                    autoFocus={c.key === "name"}
                  />
                  {fieldError
                    ? <div className="field-error">{fieldError}</div>
                    : c.hint && <div className="hint">{c.hint}</div>}
                </div>
              );
            })}
          </div>
        </div>

        <div className="modal-foot">
          <span />
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <button type="button" className="btn" onClick={onClose}>Cancel</button>
            <button className="btn primary" disabled={busy}>
              {busy ? <span className="spinner" /> : isNew ? `Add ${singular}` : "Save changes"}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}

/**
 * The tick-box grid that decides what one staff member is allowed to do.
 *
 * The catalogue comes from the SERVER (`GET /staff/capabilities`), not from a
 * constant in this bundle. The same list is what actually enforces access in
 * blueprints/api/permissions.py, so fetching it means a capability added there
 * appears here without a front-end release - and, more importantly, that the
 * labels an administrator reads can never describe something different from
 * what the API enforces.
 *
 * Two behaviours that stop this being a footgun:
 *
 *  - No boxes ticked means UNRESTRICTED, which is the opposite of what a grid
 *    of empty checkboxes looks like. So it says so, in the box, in words.
 *  - Ticking "record payments" silently grants "view invoices" on the server,
 *    because you cannot record a payment against a bill you are not allowed to
 *    open. The implied boxes are shown ticked and disabled rather than being
 *    left blank, or the screen would be describing a stricter rule than the
 *    one in force.
 */
function PermissionsField({ label, value, role, onChange, wide }) {
  const { data } = useFetch("/staff/capabilities");
  const catalogue = data?.capabilities || [];
  const implies = data?.implies || {};

  const picked = useMemo(
    () => new Set(Array.isArray(value) ? value : []),
    [value],
  );

  // What the server will add on top of what was ticked.
  const impliedByPicks = useMemo(() => {
    const out = new Set();
    picked.forEach((key) => (implies[key] || []).forEach((k) => out.add(k)));
    return out;
  }, [picked, implies]);

  const groups = useMemo(() => {
    const map = new Map();
    catalogue.forEach((c) => {
      if (!map.has(c.group)) map.set(c.group, []);
      map.get(c.group).push(c);
    });
    return [...map.entries()];
  }, [catalogue]);

  const isAdmin = role === "admin";

  function toggle(key) {
    const next = new Set(picked);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    onChange([...next]);
  }

  return (
    <div className="field" style={wide ? { gridColumn: "1 / -1" } : {}}>
      <label>{label}</label>

      {isAdmin ? (
        <div className="hint perm-note">
          Administrators can do everything. Choose another role to limit this
          account to specific tasks.
        </div>
      ) : (
        <div className="hint perm-note">
          {picked.size === 0
            ? "Nothing ticked — this account can use the whole system, as before. "
              + "Tick a box to restrict it to only those tasks."
            : `Restricted to ${picked.size} area${picked.size === 1 ? "" : "s"}. `
              + "Everything else is hidden and refused."}
        </div>
      )}

      <div className={`perm-grid${isAdmin ? " is-disabled" : ""}`}>
        {groups.map(([group, items]) => (
          <fieldset key={group} className="perm-group">
            <legend>{group}</legend>
            {items.map((c) => {
              const inherited = !picked.has(c.key) && impliedByPicks.has(c.key);
              return (
                <label key={c.key} className="check-row" title={inherited
                  ? "Granted automatically by another permission above."
                  : undefined}>
                  <input type="checkbox"
                         checked={isAdmin || picked.has(c.key) || inherited}
                         disabled={isAdmin || inherited}
                         onChange={() => toggle(c.key)} />
                  {c.label}
                  {inherited && <span className="perm-implied">auto</span>}
                </label>
              );
            })}
          </fieldset>
        ))}
      </div>
    </div>
  );
}

/** A foreign-key <select> populated from another API table. */
function LookupField({ column, value, onChange, onBlur, error, wide }) {
  const { options, loading, error: loadError } = useLookup(column.lookup, {
    valueKey: column.valueKey || "id",
    labelKey: column.labelKey || "name",
  });

  return (
    <div className={`field${error ? " has-error" : ""}`}
         style={wide && column.span === 2 ? { gridColumn: "1 / -1" } : {}}>
      <label>{column.label}{column.required && " *"}</label>
      <select className="input" value={value} onChange={onChange} onBlur={onBlur}
              disabled={loading} aria-invalid={Boolean(error)}>
        <option value="">
          {loading ? "Loading…" : loadError ? "Could not load options" : "Select…"}
        </option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>{option.label}</option>
        ))}
      </select>
      {error
        ? <div className="field-error">{error}</div>
        : loadError
          ? <div className="field-error">This list could not be loaded. Saving may fail.</div>
          : column.hint && <div className="hint">{column.hint}</div>}
    </div>
  );
}

function inputType(type) {
  if (["date", "email", "password", "tel"].includes(type)) return type;
  return "text";
}
