import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { del, get, post, put } from "../../api/client";
import { useFetch } from "../../api/useFetch";
import { useToast } from "../../context/ToastContext";
import {
  currentPlan, Empty, ErrorNote, fmtDate, inr, rupees, Loading, Pager, railFor,
  readableError, ScrollArrows, StatusPill,
} from "../ui";
import { InvoiceActions, ReceiptActions } from "./DocumentActions";
import AddonInvoice from "./AddonInvoice";
import DateField from "../../components/DateField";
import MoneyInput from "../MoneyInput";

/* ==================================================================== */
/*  Overview                                                            */
/* ==================================================================== */

export function OverviewTab({ customer, outstanding, onRefresh }) {
  return (
    <div className="cd-overview">
      <section className="panel-card cd-record">
        <div className="panel-head">Customer detail</div>
        <dl className="cd-fields">
          <Row label="Connection Type" value={customer.connection_type} />
          <Row label="Phone" value={[customer.mobile, customer.home_phone].filter(Boolean).join(", ")} />
          <Row label="Email" value={customer.email} />
          <Row label="Username" value={customer.username} mono />
          <Row label="Customer Type" value={customer.customer_type} />
          <Row label="Reg. Date" value={fmtDate(customer.registration_date)} />
          <Row label="Ref ID" value={customer.reference_id} mono />
          <Row label="Zone" value={customer.zone} />
          <Row label="Billing Type" value={customer.billing_type} />
          <Row label="Status"
               value={<StatusPill value={customer.is_active ? "active" : "inactive"} />} />
          <Row label="Tax Type" value={customer.tax_type} />
          <Row label="GSTIN" value={customer.gstin} mono />
          <Row label="PAN No" value={customer.pan} mono />
          <Row label="Adhar Card No" value={customer.aadhar} mono />
          <Row label="IP Address" value={customer.ip_address} mono />
          <Row label="Ipacct Id" value={customer.ipacct_id} mono />
          <Row label="Service Provider" value={customer.service_provider} />
        </dl>

        <h3 className="cd-subhead">Primary Address</h3>
        <p className="cd-address">{customer.primary_address || addressLine(customer) || "—"}</p>

        <h3 className="cd-subhead">Billing Address</h3>
        <p className="cd-address">{customer.billing_address || addressLine(customer) || "—"}</p>

        <h3 className="cd-subhead">Additional Information</h3>
        <dl className="cd-fields">
          <Row label="Bill Upto" value={fmtDate(customer.active_plan_end)} />
          <Row label="Outstanding"
               value={outstanding > 0
                 ? <strong className="due">{inr(outstanding)}</strong>
                 : inr(0)} />
          <Row label="Discount" value={inr(customer.discount_amount)} />
          <Row label="Location"
               value={customer.latitude && customer.longitude
                 ? <a href={`https://maps.google.com/?q=${customer.latitude},${customer.longitude}`}
                      target="_blank" rel="noreferrer noopener">
                     {customer.latitude}, {customer.longitude}
                   </a>
                 : ""} />
        </dl>

        <h3 className="cd-subhead">Documents</h3>
        <DocumentList documents={customer.documents} />
      </section>

      {/* Keyed on the customer: the note box seeds its state from the prop
          once, on mount. Without this, moving between two customer records
          without leaving the route keeps the previous customer's text in the
          box - and "Save note" would then write it onto the new record. */}
      <NotesPanel key={customer.id} customer={customer} onRefresh={onRefresh} />
    </div>
  );
}

function addressLine(customer) {
  return [customer.flat_no, customer.building, customer.area, customer.locality]
    .filter((v) => v && v !== '-')
    .join(', ') + (customer.locality ? ', Navi Mumbai, Maharashtra' : '');
}

function Row({ label, value, mono }) {
  const empty = value === null || value === undefined || value === "";
  return (
    <div className="cd-field">
      <dt>{label}</dt>
      <dd className={mono && !empty ? "mono" : undefined}>
        {empty ? <span className="muted">—</span> : value}
      </dd>
    </div>
  );
}

/** KYC slots. Missing proofs are shown too - an operator needs to see a gap. */
function DocumentList({ documents }) {
  const rows = Array.isArray(documents) ? documents : [];
  if (!rows.length) return <p className="muted small">No KYC slots configured.</p>;

  return (
    <ul className="cd-docs">
      {rows.map((doc) => (
        <li key={doc.key}>
          <span>{doc.label}{doc.doc_type ? ` (${doc.doc_type})` : ""}</span>
          {doc.url
            ? <a href={doc.url} target="_blank" rel="noreferrer noopener">View</a>
            : <em className="muted">Not uploaded</em>}
        </li>
      ))}
    </ul>
  );
}

function NotesPanel({ customer, onRefresh }) {
  const { toast } = useToast();
  const [note, setNote] = useState(customer.notes || "");
  const [busy, setBusy] = useState(false);
  const dirty = note !== (customer.notes || "");

  async function save() {
    setBusy(true);
    try {
      await put(`/customers/${customer.id}/note`, { note });
      toast.success("Note saved.");
      await onRefresh?.();
    } catch (error) {
      toast.error(error.detail || readableError(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel-card cd-notes">
      <div className="panel-head">Notes</div>
      <div className="cd-notes-body">
        <textarea value={note} rows={8} maxLength={4000}
                  placeholder="Anything the next person picking up this account should know."
                  onChange={(event) => setNote(event.target.value)} />
        <p className="hint">
          This is a single field on the customer record, so saving replaces
          what was here before rather than appending.
        </p>
        <button type="button" className="btn primary" disabled={busy || !dirty}
                onClick={save}>
          {busy ? "Saving…" : "Save note"}
        </button>
      </div>
    </section>
  );
}

/* ==================================================================== */
/*  Wallet                                                              */
/* ==================================================================== */

const EMPTY_ADJUSTMENT = { kind: "credit", amount: "", reason: "", invoice_id: "" };

/**
 * The Plan tab shows THE plan - one row, never a list.
 *
 * A customer is on one service at a time, so this printed every row with
 * status "active" and, on an account that had picked up a second open row,
 * showed two live plans side by side with two Renew buttons - and which one
 * the operator hit decided which expiry date moved. Everything this account
 * has ever been on is on the Plan History tab, which is where the rest of
 * the rows belong.
 */
export function PlanTab({ customer, plans, onAssign, onRenew, onEdit, onRefresh }) {
  const current = currentPlan(plans);
  const rows = current ? [current] : [];

  return (
    <section className="panel-card">
      <div className="cd-tab-toolbar">
        <Link className="btn primary" to={`/customers/${customer.id}/edit#username`}>
          Assign Username
        </Link>
      </div>

      {!rows.length ? (
        <Empty title="No plan assigned"
               hint="This customer has no service attached yet."
               action={<button type="button" className="btn primary"
                               onClick={() => onAssign?.()}>Assign a plan</button>} />
      ) : (
        <ScrollArrows wrapClassName="table-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th>Username</th><th>Provider</th><th>Plan</th>
                <th className="num">Total Amount</th><th>Start Date</th>
                <th>End Date</th><th className="num">Days Rem</th>
                <th>Status</th><th>Online Renewal</th><th>Plan Action</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((plan, index) => (
                <tr key={plan.id}>
                  <td className="mono">{customer.username || "—"}</td>
                  <td>{plan.plan?.service_provider || customer?.service_provider || "—"}</td>
                  <td>{plan.plan_name || "—"}</td>
                  <td className="num">{inr(plan.price_monthly)}</td>
                  <td>{fmtDate(plan.start_date)}</td>
                  <td>{fmtDate(plan.end_date)}</td>
                  <td className={`num ${plan.days_left < 0 ? "due" : ""}`}>
                    {plan.days_left ?? "—"}
                  </td>
                  <td><StatusPill value={plan.status} /></td>
                  <td>
                    <OnlineRenewalToggle plan={plan} customer={customer}
                                         onRefresh={onRefresh} />
                  </td>
                  <td>
                    <div className="row-actions">
                      <button type="button" className="btn sm primary"
                              onClick={() => onAssign?.(plan)}>Assign/Change</button>
                      <button type="button" className="btn sm"
                              onClick={() => onRenew?.(plan)}>Renew</button>
                      <button type="button" className="btn sm"
                              onClick={() => onEdit?.(plan)}>Edit</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </ScrollArrows>
      )}

      {plans.length > rows.length && (
        <p className="hint padded">
          {plans.length - rows.length} other plan(s) are on the Plan History tab.
        </p>
      )}
    </section>
  );
}

/**
 * The Online Renewal column, as a switch rather than a read-out.
 *
 * It printed `auto_renew`, which is a different thing wearing this label:
 * auto_renew is the billing run's switch - whether the office raises the next
 * invoice by itself - so turning it off to stop a customer renewing from the
 * portal would also have stopped their bills. This writes `online_renewal`,
 * which does exactly one thing: it decides whether the portal's Renew and
 * Change plan buttons work for this customer. Their bills, their plan and
 * their login are untouched either way.
 *
 * Switching OFF asks first. It takes a working button away from somebody who
 * is not in the room to notice, and the operator should mean it. Switching
 * back on is immediate - restoring something is not a decision worth
 * interrupting.
 */
function OnlineRenewalToggle({ plan, customer, onRefresh }) {
  const { toast, confirm } = useToast();
  const [busy, setBusy] = useState(false);
  // Absent reads as on: rows written before this column existed have no
  // value, and a missing setting must not lock a customer out of a screen
  // that worked yesterday.
  const on = plan.online_renewal !== false;

  async function toggle() {
    if (busy) return;
    const next = !on;
    if (!next) {
      const name = customer?.full_name || "This customer";
      const ok = await confirm({
        title: "Switch off online renewal?",
        message: `${name} will no longer be able to renew or change this plan `
          + "from the customer portal - they will be told to contact the "
          + "office. Their bills are still raised as usual and they can still "
          + "sign in and pay. You can switch it back on at any time.",
        confirmLabel: "Switch off",
        tone: "danger",
      });
      if (!ok) return;
    }

    setBusy(true);
    try {
      await put(`/customer-plans/${plan.id}`, { online_renewal: next });
      toast.success(next
        ? "Online renewal is on - this customer can renew from the portal."
        : "Online renewal is off - this customer must renew at the office.");
      await onRefresh?.();
    } catch (error) {
      toast.error(error.detail || readableError(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <button type="button" className={`toggle-pill${on ? " is-on" : " is-off"}`}
            disabled={busy} onClick={toggle} aria-pressed={on}
            title={on
              ? "This customer can renew from the portal. Click to switch off."
              : "This customer cannot renew from the portal. Click to switch on."}>
      {busy ? "…" : on ? "Yes" : "No"}
    </button>
  );
}

/* ==================================================================== */
/*  Invoices                                                            */
/* ==================================================================== */

/**
 * Pending Invoice - what is owed, and the one place money is taken.
 *
 * Raising a bill and collecting for it used to happen in the same submit on
 * the Addon Invoice form. That made a mis-keyed charge impossible to withdraw,
 * because it arrived already settled, and it forced a customer paying for a
 * renewal AND an addon to be entered as two separate transactions.
 *
 * Now bills accumulate here and one payment entry clears whichever of them the
 * customer is settling, with the discount spread across them oldest first.
 */
export function PendingInvoiceTab({ customer, onRefresh }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [showAddon, setShowAddon] = useState(false);
  const [editing, setEditing] = useState(null);
  const [selected, setSelected] = useState(() => new Set());
  const [busy, setBusy] = useState(false);
  const { toast, confirm } = useToast();

  const [form, setForm] = useState({
    amount: "", discount_amount: "", discount_reason: "", payment_mode: "",
    bank_name: "", transaction_no: "", transaction_date: "",
    book_receipt_no: "", remark: "", payment_date: "",
  });
  const [errors, setErrors] = useState({});

  const load = useCallback(() => {
    setLoading(true);
    return get(`/customers/${customer.id}/pending-invoices`)
      .then((response) => {
        const payload = response?.data ?? response;
        setData(payload);
        const live = new Set((payload?.invoices || []).map((i) => i.id));
        setSelected((prev) => {
          const next = new Set([...prev].filter((id) => live.has(id)));
          // First load: pre-select everything, since settling the lot is the
          // common case at the counter.
          return prev.size === 0 ? live : next;
        });
      })
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [customer.id]);

  useEffect(() => { load(); }, [load]);

  const rows = data?.invoices || [];
  const chosen = rows.filter((r) => selected.has(r.id));
  const chosenTotal = chosen.reduce((sum, r) => sum + Number(r.balance || 0), 0);
  const discount = Number(form.discount_amount || 0);
  const payable = Math.max(0, chosenTotal - discount);

  const referenced = useMemo(
    () => new Set(data?.referenced_modes || []), [data]);
  const needsReference = Boolean(form.payment_mode)
    && referenced.has(form.payment_mode);

  // Default the amount to whatever is selected, until the operator types.
  useEffect(() => {
    setForm((f) => (f.amount === "" || f.__auto
      ? { ...f, amount: payable ? rupees(payable) : "", __auto: true }
      : f));
  }, [payable]);

  const set = (key) => (event) => {
    setForm((f) => ({ ...f, [key]: event.target.value,
                      __auto: key === "amount" ? false : f.__auto }));
    setErrors((e) => ({ ...e, [key]: undefined }));
  };

  function toggle(id) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function remove(row) {
    const confirmed = await confirm({
      title: `Delete ${row.invoice_no}?`,
      message: `${row.caption} — ${inr(row.total_amount)}. Deleting removes `
        + "the bill from the ledger entirely. If any payment entry is linked "
        + "to it, that entry is detached and stops counting — prefer Cancel "
        + "so the trace of money stays visible. This cannot be undone.",
      confirmLabel: "Delete invoice",
      tone: "danger",
    });
    if (!confirmed) return;

    try {
      await del(`/invoices/${row.id}`);
      toast.success(`${row.invoice_no} deleted.`);
      await load();
      await onRefresh?.();
    } catch (error) {
      toast.error(error.detail || readableError(error));
    }
  }

  async function cancel(row) {
    const confirmed = await confirm({
      title: `Cancel ${row.invoice_no}?`,
      message: `${row.caption} — ${inr(row.total_amount)}. The bill stays on `
        + "the record marked cancelled, with any payment entries still "
        + "visible against it. This cannot be undone.",
      confirmLabel: "Cancel invoice",
      tone: "danger",
    });
    if (!confirmed) return;

    try {
      const response = await post(`/invoices/${row.id}/cancel`, {});
      const payload = response?.data ?? response;
      toast.success(`${payload.invoice_no || row.invoice_no} cancelled.`);
      await load();
      await onRefresh?.();
    } catch (error) {
      toast.error(error.detail || readableError(error));
    }
  }

  function validate() {
    const found = {};
    if (!chosen.length) found.invoices = "Choose at least one invoice.";
    if (!(Number(form.amount) > 0)) found.amount = "Enter the amount received.";
    if (Number(form.amount) > payable) found.amount = `Only ${inr(payable)} is due.`;
    if (discount > chosenTotal) found.discount_amount = "More than is outstanding.";
    if (discount > 0 && !form.discount_reason) {
      found.discount_reason = "Pick a discount type.";
    }
    if (!form.payment_mode) found.payment_mode = "Choose how they paid.";
    setErrors(found);
    return Object.keys(found).length === 0;
  }

  async function submit(event) {
    event.preventDefault();
    if (!validate()) return;

    setBusy(true);
    try {
      const response = await post(`/customers/${customer.id}/payments`, {
        invoice_ids: [...selected],
        amount: Number(form.amount),
        discount_amount: discount || 0,
        discount_reason: form.discount_reason || undefined,
        payment_mode: form.payment_mode,
        bank_name: form.bank_name || undefined,
        transaction_no: form.transaction_no || undefined,
        transaction_date: form.transaction_date || undefined,
        book_receipt_no: form.book_receipt_no || undefined,
        payment_date: form.payment_date || undefined,
        remark: form.remark || undefined,
      });
      const payload = response?.data ?? response;

      toast.success(
        `${inr(payload.amount)} received${payload.settled.length
          ? `, ${payload.settled.join(", ")} settled` : ""}.`
        + (payload.remaining_due > 0
          ? ` ${inr(payload.remaining_due)} still outstanding.` : ""));

      setForm({ amount: "", discount_amount: "", discount_reason: "",
                payment_mode: "", bank_name: "", transaction_no: "",
                transaction_date: "", book_receipt_no: "", remark: "",
                payment_date: "", __auto: true });
      setSelected(new Set());
      await load();
      await onRefresh?.();
    } catch (error) {
      toast.error(error.detail || readableError(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel-card">
      <div className="cd-tab-toolbar">
        <button type="button" className="btn primary"
                onClick={() => setShowAddon((open) => !open)}>
          {showAddon ? "Close" : "Addon Invoice"}
        </button>
        {data?.total_outstanding > 0 && (
          <span className="pending-total">
            <strong>{inr(data.total_outstanding)}</strong> outstanding
            across {data.count} invoice(s)
          </span>
        )}
      </div>

      {showAddon && (
        <AddonInvoice customer={customer}
                      onCancel={() => setShowAddon(false)}
                      onDone={async () => {
                        setShowAddon(false);
                        await load();
                        await onRefresh?.();
                      }} />
      )}

      {editing && (
        <EditInvoiceDialog invoice={editing} onClose={() => setEditing(null)}
                           onDone={async () => {
                             setEditing(null);
                             await load();
                             await onRefresh?.();
                           }} />
      )}

      {loading ? <Loading label="Loading what is owed" />
        : !rows.length ? (
          <Empty title="Nothing pending"
                 hint="Every invoice on this account is settled." />
        ) : (
          <form onSubmit={submit} className="pay-entry">
            <ScrollArrows wrapClassName="table-wrap">
              <table className="tbl pending-table">
                <thead>
                  <tr>
                    <th className="tick">
                      <input type="checkbox"
                             checked={rows.every((r) => selected.has(r.id))}
                             aria-label="Select every pending invoice"
                             onChange={() => setSelected(
                               rows.every((r) => selected.has(r.id))
                                 ? new Set() : new Set(rows.map((r) => r.id)))} />
                    </th>
                    <th>Invoice</th><th>Caption</th><th>Type</th>
                    <th>Date</th><th>Due</th>
                    <th className="num">Amount</th><th className="num">Paid</th>
                    <th className="num">Balance</th><th>Bill</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.id} className={selected.has(row.id) ? "is-selected" : undefined}>
                      <td className="tick">
                        <input type="checkbox" checked={selected.has(row.id)}
                               aria-label={`Pay ${row.invoice_no}`}
                               onChange={() => toggle(row.id)} />
                      </td>
                      <td><Link to={`/invoices/${row.id}`}>{row.invoice_no}</Link></td>
                      <td>{row.caption}</td>
                      <td><span className={`type-tag ${row.invoice_type}`}>{row.invoice_type}</span></td>
                      <td>{fmtDate(row.issue_date)}</td>
                      <td>{fmtDate(row.due_date)}</td>
                      <td className="num">{inr(row.total_amount)}</td>
                      {/* Nothing paid on a bill is zero paid, not unknown. */}
                      <td className="num">{inr(row.paid_amount || 0)}</td>
                      <td className="num"><strong>{inr(row.balance)}</strong></td>
                      <td className="pending-actions">
                        <InvoiceActions invoice={row} compact only={["pdf", "whatsapp"]} />
                        <button type="button" className="link-edit"
                                onClick={() => setEditing(row)}>Edit</button>
                        <button type="button" className="link-danger"
                                onClick={() => cancel(row)}>Cancel</button>
                        {row.can_delete ? (
                          <button type="button" className="link-danger"
                                  onClick={() => remove(row)}>Delete</button>
                        ) : (
                          <span className="muted" title="Money has been paid against this">
                            part paid
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </ScrollArrows>

            {errors.invoices && <p className="field-error">{errors.invoices}</p>}

            <div className="pay-grid">
              <label>
                {/* This picks a REASON from the Discount Master - it never
                    chose between a percentage and an amount, but "Discount
                    type" beside a "Discount" box read as though it did. */}
                <span>Discount reason</span>
                <select value={form.discount_reason} onChange={set("discount_reason")}>
                  <option value="">-Select reason-</option>
                  {(data?.discount_reasons || []).map((r) => (
                    <option key={r.id} value={r.name}>{r.name}</option>
                  ))}
                </select>
                {errors.discount_reason && (
                  <small className="field-error">{errors.discount_reason}</small>
                )}
              </label>
              <label>
                <span>Discount (₹)</span>
                <MoneyInput value={form.discount_amount} onChange={set("discount_amount")} />
                {errors.discount_amount && (
                  <small className="field-error">{errors.discount_amount}</small>
                )}
              </label>
              <label>
                <span>Amount received</span>
                <MoneyInput value={form.amount} onChange={set("amount")} />
                {errors.amount && <small className="field-error">{errors.amount}</small>}
              </label>
              <label>
                <span>Mode</span>
                <select value={form.payment_mode} onChange={set("payment_mode")}>
                  <option value="">-Select Mode-</option>
                  {(data?.payment_modes || []).map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
                {errors.payment_mode && (
                  <small className="field-error">{errors.payment_mode}</small>
                )}
              </label>
              <DateField label="Payment date" value={form.payment_date}
                         onChange={(v) => setForm((f) => ({ ...f, payment_date: v }))} />
            </div>

            {/* Only for the modes that actually carry a reference. */}
            {needsReference && (
              <div className="pay-grid">
                <label>
                  <span>Bank name</span>
                  <input value={form.bank_name} onChange={set("bank_name")} />
                </label>
                <label>
                  <span>Transaction no.</span>
                  <input value={form.transaction_no} onChange={set("transaction_no")} />
                </label>
                <DateField label="Transaction date" value={form.transaction_date}
                           onChange={(v) => setForm((f) => ({ ...f, transaction_date: v }))} />
              </div>
            )}

            <div className="pay-grid">
              <label>
                <span>Book receipt no.</span>
                <input value={form.book_receipt_no} onChange={set("book_receipt_no")} />
              </label>
              <label className="span-2">
                <span>Remark</span>
                <input value={form.remark} onChange={set("remark")} />
              </label>
            </div>

            <div className="pay-summary">
              <span>{chosen.length} invoice(s) selected · {inr(chosenTotal)}</span>
              {discount > 0 && <span>less discount {inr(discount)}</span>}
              <strong>Payable {inr(payable)}</strong>
              <button className="btn primary" disabled={busy || !chosen.length}>
                {busy ? "Saving…" : "Submit payment"}
              </button>
            </div>
          </form>
        )}
    </section>
  );
}

/**
 * Correct a bill: amount, and the period it covers.
 *
 * Only those three. An invoice number or customer that can be changed after
 * the fact is not a record of anything, and the API refuses the whole edit
 * once a payment is attached - altering the amount underneath a payment turns
 * a settled bill into an over- or under-payment with nothing saying why.
 */
function EditInvoiceDialog({ invoice, onClose, onDone }) {
  const { toast } = useToast();
  const [form, setForm] = useState({
    amount: rupees(invoice.total_amount),
    period_start: invoice.period_start || "",
    period_end: invoice.period_end || "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const amount = Number(form.amount || 0);
  const discount = Number(invoice.discount_amount || 0);

  async function submit(event) {
    event.preventDefault();
    if (!(amount > 0)) return setError("The amount must be more than zero.");
    if (discount > amount) {
      return setError(`This bill carries a ${inr(discount)} discount, so the `
        + "amount cannot be less than that.");
    }
    if (form.period_start && form.period_end
        && form.period_end <= form.period_start) {
      return setError("The expiry date must be after the renew date.");
    }

    setBusy(true);
    setError(null);
    try {
      const response = await put(`/invoices/${invoice.id}`, form);
      const data = response?.data ?? response;
      toast.success(data.changed?.length
        ? `${invoice.invoice_no} updated: ${data.changed.join("; ")}.`
        : "Nothing changed.");
      await onDone?.();
    } catch (editError) {
      setError(editError.detail || readableError(editError));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-scrim" onClick={onClose}>
      <div className="modal-card edit-invoice" onClick={(e) => e.stopPropagation()}>
        <div className="panel-head">Edit {invoice.invoice_no}</div>
        <form onSubmit={submit}>
          {error && <p className="renew-warn">{error}</p>}

          <div className="pay-grid">
            <label>
              <span>Bill amount</span>
              <MoneyInput autoFocus value={form.amount}
                          onChange={(e) => setForm({ ...form, amount: e.target.value })} />
            </label>
            <DateField label="Renew date" value={form.period_start}
                       onChange={(v) => setForm({ ...form, period_start: v })} />
            <DateField label="Expiry date" value={form.period_end}
                       onChange={(v) => setForm({ ...form, period_end: v })} />
          </div>

          <p className="hint">
            {discount > 0 && <>A {inr(discount)} discount is already on this bill. </>}
            Changing the expiry also moves the customer&apos;s plan expiry, so the
            bill and the connection do not disagree.
          </p>

          <div className="row-actions" style={{ justifyContent: "flex-end" }}>
            <button type="button" className="btn" onClick={onClose}>Cancel</button>
            <button className="btn primary" disabled={busy}>
              {busy ? "Saving…" : "Save changes"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export function InvoiceHistoryTab({ invoices }) {
  if (!invoices.length) return <Empty title="No invoices yet" />;
  return (
    <section className="panel-card">
      <div className="panel-head">Invoice history</div>
      <InvoiceTable rows={invoices} />
    </section>
  );
}

function InvoiceTable({ rows, showPending = false }) {
  return (
    <ScrollArrows wrapClassName="table-wrap">
      <table className="tbl">
        <thead>
          <tr>
            <th>Invoice</th><th>Caption</th><th>Invoice Date</th>
            <th>Due Date</th><th>Status</th><th className="num">Amount</th>
            {showPending && <th className="num">Pending</th>}
            <th>Action</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((invoice) => (
            <tr key={invoice.id} className={railFor("invoice", invoice.status)}>
              <td className="mono">
                <Link to={`/invoices/${invoice.id}`}>{invoice.invoice_no}</Link>
              </td>
              <td>{invoice.caption || "—"}</td>
              <td>{fmtDate(invoice.issue_date)}</td>
              <td>{fmtDate(invoice.due_date)}</td>
              <td><StatusPill value={invoice.status} /></td>
              <td className="num">{invoice.status === 'cancelled' ? '₹0' : inr(invoice.total_amount)}</td>
              {showPending && (
                <td className="num"><strong className="due">{invoice.status === 'cancelled' ? '₹0' : inr(invoice.balance)}</strong></td>
              )}
              <td><InvoiceActions invoice={invoice} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </ScrollArrows>
  );
}

export function PaymentHistoryTab({ payments, onRefresh }) {
  if (!payments.length) return <Empty title="No payments recorded yet" />;
  return (
    <section className="panel-card">
      <div className="panel-head">Payment history</div>
      <ScrollArrows wrapClassName="table-wrap">
        <table className="tbl">
          <thead>
            <tr>
              <th>Date</th><th>Receipt</th><th>Book Receipt No.</th>
              <th className="num">Received</th><th>Against Invoice</th>
              <th>Mode</th><th>Discount</th><th>Received By</th>
              <th>Status</th><th>Action</th>
            </tr>
          </thead>
          <tbody>
            {payments.map((payment) => (
              <tr key={payment.id}
                  className={railFor("payment", payment.status, payment.needs_authorization)}>
                <td>{fmtDate(payment.payment_date)}</td>
                <td className="mono">R{payment.id}</td>
                <td className="mono">{payment.book_receipt_no || "—"}</td>
                {/* A return is a negative row. Shown as a negative, in red,
                    labelled - not as a positive number that silently makes
                    the column stop adding up. */}
                <td className={`num${Number(payment.amount) < 0 ? " due" : ""}`}>
                  {Number(payment.amount) < 0
                    ? <>−{inr(Math.abs(Number(payment.amount)))} <small>return</small></>
                    : inr(payment.amount)}
                </td>
                <td className="mono">
                  {payment.invoice_id
                    ? <Link to={`/invoices/${payment.invoice_id}`}>{payment.invoice_no}</Link>
                    : "—"}
                </td>
                <td>{payment.payment_mode || "—"}</td>
                <td>{Number(payment.discount_amount) > 0
                  ? `Yes — ${inr(payment.discount_amount)}` : "No"}</td>
                {/* `received_by_label` already says Self Renew for a payment
                    the customer made from the portal, where there is no staff
                    member to name. */}
                <td>{payment.received_by_label || payment.received_by || "—"}</td>
                <td><StatusPill value={payment.status} kind="payment" /></td>
                <td><ReceiptActions payment={payment} onChanged={onRefresh} /></td>
              </tr>
            ))}
          </tbody>
          </table>
      </ScrollArrows>
    </section>
  );
}

/* ==================================================================== */
/*  Simple history tabs                                                 */
/* ==================================================================== */

export function InventoryTab({ customerId }) {
  const { data, loading, error, refetch } = useFetch(`/customers/${customerId}/inventory`);
  const rows = Array.isArray(data) ? data : [];

  return (
    <TablePanel title="Inventory" loading={loading} error={error} onRetry={refetch}
                empty={!rows.length}
                emptyTitle="No hardware issued"
                emptyHint="Routers and ONTs assigned to this customer appear here.">
      <table className="tbl">
        <thead>
          <tr><th>Product</th><th>SKU</th><th>Serial No.</th>
            <th>Assigned</th><th>Status</th></tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>{row.product || "—"}</td>
              <td className="mono">{row.sku || "—"}</td>
              <td className="mono">{row.serial_number || "—"}</td>
              <td>{fmtDate(row.assigned_date)}</td>
              <td><StatusPill value={row.status} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </TablePanel>
  );
}

export function MessageLogTab({ customerId }) {
  const [page, setPage] = useState(1);
  const { data, meta, loading, error, refetch } =
    useFetch(`/customers/${customerId}/messages`, { page });
  const rows = Array.isArray(data) ? data : [];

  return (
    <TablePanel title="SMS & WhatsApp log" loading={loading} error={error}
                onRetry={refetch} empty={!rows.length}
                emptyTitle="Nothing sent yet"
                emptyHint="Every message this customer is sent is logged here."
                footer={<Pager meta={meta} onPage={setPage} />}>
      <table className="tbl">
        <thead>
          <tr><th>Sent</th><th>Channel</th><th>Type</th><th>To</th>
            <th>Message</th><th>Status</th></tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>{fmtDate(row.created_at)}</td>
              <td>{row.channel || "—"}</td>
              <td>{row.template_type || "—"}</td>
              <td className="mono">{row.phone || "—"}</td>
              <td className="log-body">{row.body || "—"}</td>
              <td>
                <StatusPill value={row.status} />
                {row.error && <div className="muted small">{row.error}</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TablePanel>
  );
}

export function PlanHistoryTab({ customerId }) {
  const { data, loading, error, refetch } = useFetch(`/customers/${customerId}/plan-history`);
  const rows = Array.isArray(data) ? data : [];

  return (
    <TablePanel title="Plan history" loading={loading} error={error} onRetry={refetch}
                empty={!rows.length} emptyTitle="No plans on record">
      <table className="tbl">
        <thead>
          <tr><th>Plan</th><th>Provider</th><th className="num">Price</th>
            <th>Start</th><th>End</th><th>Auto renew</th><th>Status</th></tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>{row.plan_name || "—"}</td>
              <td>{row.plan?.service_provider || "—"}</td>
              <td className="num">{inr(row.price_monthly)}</td>
              <td>{fmtDate(row.start_date)}</td>
              <td>{fmtDate(row.end_date)}</td>
              <td>{row.auto_renew ? "Yes" : "No"}</td>
              <td><StatusPill value={row.status} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </TablePanel>
  );
}

export function CustomerLogTab({ customerId }) {
  const [page, setPage] = useState(1);
  const { data, meta, loading, error, refetch } =
    useFetch(`/customers/${customerId}/logs`, { page });
  const rows = Array.isArray(data) ? data : [];

  return (
    <TablePanel title="Customer log" loading={loading} error={error} onRetry={refetch}
                empty={!rows.length} emptyTitle="No activity logged yet"
                emptyHint="Actions taken on this account are recorded here."
                footer={<Pager meta={meta} onPage={setPage} />}>
      <table className="tbl">
        <thead>
          <tr><th>When</th><th>Action</th><th>Details</th><th>By</th></tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>{fmtDate(row.created_at)}</td>
              <td>{row.action || "—"}</td>
              <td className="log-body">{row.details || "—"}</td>
              <td>{row.user || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TablePanel>
  );
}

export function LedgerTab({ customerId }) {
  const { data, loading, error, refetch } = useFetch(`/customers/${customerId}/ledger`);
  const rows = data?.entries || [];

  return (
    <TablePanel title="Payment ledger" loading={loading} error={error} onRetry={refetch}
                empty={!rows.length} emptyTitle="Nothing on the ledger yet"
                header={data && (
                  <div className="ledger-summary">
                    <span>Closing balance
                      <strong className={data.closing_balance > 0 ? "due" : ""}>
                        {inr(data.closing_balance)}
                      </strong>
                    </span>
                    {/* Only when there is something in it. The wallet was
                        switched off, so on every current account this said
                        "Wallet ₹0" - a permanent zero that reads as a live
                        feature nobody is using. */}
                    {Number(data.wallet_balance) > 0 && (
                      <span>Wallet credit
                        <strong>{inr(data.wallet_balance)}</strong>
                      </span>
                    )}
                  </div>
                )}>
      <table className="tbl">
        <thead>
          <tr>
            <th>Date</th><th>Type</th><th>Reference</th><th>Description</th>
            <th className="num">Debit</th><th className="num">Credit</th>
            <th className="num">Balance</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${row.type}-${row.invoice_id}-${row.payment_id}-${index}`}>
              <td>{fmtDate(row.date)}</td>
              <td className="cap">{row.type}</td>
              <td className="mono">{row.reference || "—"}</td>
              <td>{row.description || "—"}</td>
              {/* Both sides always print a figure. A ledger where one column
                  is a dash and the other a number cannot be added up by eye,
                  and a reader cannot tell a nil movement from a missing one. */}
              <td className="num">{inr(row.debit || 0)}</td>
              <td className="num credit">{inr(row.credit || 0)}</td>
              <td className={`num ${row.balance > 0 ? "due" : ""}`}>{inr(row.balance)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TablePanel>
  );
}

/** Shared shell so every history tab handles loading/error/empty the same way. */
function TablePanel({ title, loading, error, onRetry, empty, emptyTitle,
                      emptyHint, header, footer, children }) {
  return (
    <section className="panel-card">
      <div className="panel-head">{title}</div>
      {header}
      <ErrorNote error={error} onRetry={onRetry} />
      {loading ? <Loading label={`Loading ${String(title).toLowerCase()}`} />
        : empty ? <Empty title={emptyTitle} hint={emptyHint} />
          : <ScrollArrows wrapClassName="table-wrap">{children}</ScrollArrows>}
      {footer}
    </section>
  );
}
