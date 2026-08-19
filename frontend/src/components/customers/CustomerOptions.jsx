import { useEffect, useMemo, useRef, useState } from "react";
import { del, get, post, put } from "../../api/client";
import { useToast } from "../../context/ToastContext";
import { fmtDate, inr, rupees, readableError, ScrollArrows } from "../ui";
import PlanPicker from "./PlanPicker";
import DateField from "../../components/DateField";
import MoneyInput from "../MoneyInput";

/* ==================================================================== */
/*  Options dropdown                                                    */
/* ==================================================================== */

/**
 * The account's action menu.
 *
 * Ticketing is deliberately absent: it was removed from this build, so an
 * "Add Ticket" entry would open onto nothing. Everything listed here has a
 * working endpoint behind it.
 */
/**
 * Chase a payment, from anywhere on the customer's file.
 *
 * A bell beside Options rather than a control inside the Overview tab: this
 * is the action an operator takes while the customer is on the phone, and
 * they are just as likely to be looking at the plan or the payment history
 * when they decide to send it. The header is on every tab; Overview is not.
 *
 * It goes quiet - greyed, with the reason in the tooltip - when there is
 * nothing to chase or no number to chase it on. A bell that is always
 * pressable and sometimes answers "nothing outstanding" trains people to
 * ignore what it says.
 */
export function DueReminderBell({ customer, outstanding, busy, onSend }) {
  const due = Number(outstanding || 0);
  const mobile = (customer.mobile || "").trim();
  const blocked = due <= 0 || !mobile;

  const title = !mobile
    ? "No mobile number on file, so there is nowhere to send a reminder."
    : due <= 0
      ? "Nothing is outstanding on this account."
      : `Send the WhatsApp due reminder for ${inr(due)} to ${mobile}, `
        + "with a link to the latest bill.";

  return (
    <button type="button"
            className={`cd-bell${due > 0 && mobile ? " is-due" : ""}`}
            disabled={blocked || busy} title={title}
            aria-label={`Send due reminder${due > 0 ? ` for ${inr(due)}` : ""}`}
            onClick={onSend}>
      {/* Drawn rather than pulled from the Font Awesome CDN. Everything else
          here degrades to a text label if that stylesheet does not arrive;
          an icon-only control degrades to an empty square. */}
      {busy
        ? <span className="spinner" aria-hidden="true" />
        : (
          <svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true"
               fill="none" stroke="currentColor" strokeWidth="1.9"
               strokeLinecap="round" strokeLinejoin="round">
            <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
            <path d="M13.73 21a2 2 0 0 1-3.46 0" />
          </svg>
        )}
      {due > 0 && mobile && <span className="cd-bell-dot" aria-hidden="true" />}
    </button>
  );
}

export function OptionsMenu({ customer, isAdmin, outstanding, onPick }) {
  const [open, setOpen] = useState(false);
  const wrapper = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    function onDocumentClick(event) {
      if (!wrapper.current?.contains(event.target)) setOpen(false);
    }
    function onKey(event) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDocumentClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocumentClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const hasDiscount = Number(customer.discount_amount) > 0
    || Number(customer.discount_percent) > 0;   // legacy rows may still be %

  const due = Number(outstanding || 0);
  const mobile = (customer.mobile || "").trim();

  const items = [
    { key: "edit", label: "Edit" },
    { key: "addon", label: "Addon Invoice" },
    {
      key: "reminder",
      // The amount is in the label because this is a decision, not a
      // navigation: the operator wants to know what the customer is about
      // to be told before they tell them.
      label: due > 0 ? `WhatsApp Due Reminder (${inr(due)})`
        : "WhatsApp Due Reminder",
      disabled: due <= 0 || !mobile,
      title: !mobile ? "No mobile number on file."
        : due <= 0 ? "Nothing is outstanding on this account." : undefined,
    },
    { divider: true },
    { key: "discount", label: "Add Discount", admin: true },
    {
      key: "clear-discount",
      label: "Cancel Discount",
      admin: true,
      disabled: !hasDiscount,
      title: hasDiscount ? undefined : "No discount is set on this account.",
    },
    { key: "notes", label: "Add Notes" },
    { key: "ledger", label: "Payment Ledger" },
    { divider: true },
    { key: "reset-mac", label: "Reset Mac", admin: true },
    { key: "reset-password", label: "Reset Password", admin: true },
    { divider: true },
    {
      key: "enable",
      label: "Enable Customer",
      admin: true,
      disabled: customer.is_active,
      title: customer.is_active ? "This customer is already enabled." : undefined,
    },
    {
      key: "disable",
      label: "Disable Customer",
      admin: true,
      disabled: !customer.is_active,
      title: customer.is_active ? undefined : "This customer is already disabled.",
    },
    { key: "terminate", label: "Terminate Customer", admin: true, danger: true },
    // Last, below Terminate, and the only irreversible entry in the list.
    // The server refuses it once there is any billing history, so for most
    // real customers this fails with an explanation rather than deleting
    // anything - it exists for the duplicate and the typo.
    { key: "delete", label: "Delete Customer", admin: true, danger: true },
  ];

  return (
    <div className="cd-options" ref={wrapper}>
      <button type="button" className="btn primary" aria-haspopup="menu"
              aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        Options <i className="fas fa-caret-down" aria-hidden="true" />
      </button>

      {open && (
        <ul className="cd-options-menu" role="menu">
          {items.map((item, index) => {
            if (item.divider) return <li key={`d${index}`} className="divider" />;
            // Admin-only entries stay visible but disabled: hiding them makes
            // staff think the feature does not exist rather than that they
            // lack the rights to use it.
            const blocked = (item.admin && !isAdmin) || item.disabled;
            const title = item.admin && !isAdmin
              ? "Only an administrator can do this."
              : item.title;
            return (
              <li key={item.key} role="none">
                <button type="button" role="menuitem" disabled={blocked}
                        title={title}
                        className={item.danger ? "danger" : undefined}
                        onClick={() => { setOpen(false); onPick(item.key); }}>
                  {item.label}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/* ==================================================================== */
/*  Modal shell                                                         */
/* ==================================================================== */

export function Modal({ title, onClose, children, width }) {
  const card = useRef(null);

  useEffect(() => {
    function onKey(event) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    card.current?.querySelector("input, select, textarea, button")?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal-card" ref={card} role="dialog" aria-modal="true"
           aria-label={title} style={width ? { maxWidth: width } : undefined}>
        <header className="modal-head">
          <h2>{title}</h2>
          <button type="button" className="modal-close" aria-label="Close"
                  onClick={onClose}>×</button>
        </header>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}

/** Shared submit plumbing so each dialog does not repeat try/catch/busy. */
function useSubmit({ onDone, onClose }) {
  const { toast } = useToast();
  const [busy, setBusy] = useState(false);

  async function run(fn, successMessage) {
    if (busy) return undefined;          // duplicate-submit guard
    setBusy(true);
    try {
      const result = await fn();
      const payload = result?.data ?? result;
      if (successMessage) {
        toast.success(typeof successMessage === "function"
          ? successMessage(payload) : successMessage);
      }
      await onDone?.();
      onClose?.();
      return payload;
    } catch (error) {
      toast.error(error.detail || readableError(error));
      return undefined;
    } finally {
      setBusy(false);
    }
  }

  return { busy, run, toast };
}

/* ==================================================================== */
/*  Dialogs                                                             */
/* ==================================================================== */

/**
 * A discount is a flat amount in rupees. Nothing here is a percentage.
 *
 * The dialog used to offer a choice between "percent of the bill" and "flat
 * amount", and the two were mutually exclusive - so the same account could be
 * carrying either, the header chip showed "10%" on one customer and "Rs.150"
 * on the next, and no screen could total what was being given away. Money
 * that appears in two different units in the same column is money nobody
 * checks. One unit, everywhere.
 *
 * The API still accepts `discount_type`, so an existing percentage keeps
 * working until it is edited - we send `amount`, which clears it.
 */
export function DiscountDialog({ customer, onClose, onDone }) {
  const { busy, run } = useSubmit({ onDone, onClose });
  const [value, setValue] = useState(String(customer.discount_amount || ""));

  const numeric = Number(value);
  const hadPercent = Number(customer.discount_percent) > 0;

  return (
    <Modal title="Add discount" onClose={onClose}>
      <form onSubmit={(event) => {
        event.preventDefault();
        run(() => post(`/customers/${customer.id}/discount`,
                       { discount_type: "amount", value: numeric }),
            "Discount saved.");
      }}>
        <div className="dlg-grid">
          <label>
            <span>Discount amount (₹)</span>
            <MoneyInput value={value} required autoFocus
                        onChange={(e) => setValue(e.target.value)} />
          </label>
        </div>
        {hadPercent && (
          <p className="hint">
            This account currently has a {customer.discount_percent}% discount.
            Saving replaces it with the flat amount above.
          </p>
        )}
        <p className="hint">
          Taken off every bill raised for this customer, in rupees.
        </p>
        <DialogButtons busy={busy} onClose={onClose} disabled={!(numeric >= 0)} />
      </form>
    </Modal>
  );
}

export function ResetMacDialog({ customer, onClose, onDone }) {
  const { busy, run } = useSubmit({ onDone, onClose });
  const [mac, setMac] = useState("");
  const valid = /^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$/.test(mac.trim());

  return (
    <Modal title="Reset MAC address" onClose={onClose}>
      <form onSubmit={(event) => {
        event.preventDefault();
        run(() => post(`/customers/${customer.id}/reset-mac`,
                       { mac_address: mac.trim() }),
            "MAC reset with the provider.");
      }}>
        <label className="dlg-block">
          <span>New MAC address</span>
          <input value={mac} placeholder="AA:BB:CC:DD:EE:FF" required
                 onChange={(e) => setMac(e.target.value)} />
        </label>
        <p className="hint">
          This is pushed to the upstream provider and is not stored here, so
          the customer record will not show it afterwards.
        </p>
        {mac && !valid && <p className="field-error">Use the format AA:BB:CC:DD:EE:FF.</p>}
        <DialogButtons busy={busy} onClose={onClose} disabled={!valid} label="Reset" />
      </form>
    </Modal>
  );
}

/** Assign or change the plan. The package table is <PlanPicker>, shared with
    the Add Customer form so the two cannot offer different catalogues. */
export function AssignPlanDialog({ customer, current, onClose, onDone }) {
  const { busy, run } = useSubmit({ onDone, onClose });
  const [planId, setPlanId] = useState(current?.plan_id ? String(current.plan_id) : "");
  const [startDate, setStartDate] = useState(new Date().toISOString().slice(0, 10));

  return (
    <Modal title={current ? "Change plan" : "Assign plan"} onClose={onClose} width="720px">
      <form onSubmit={(event) => {
        event.preventDefault();
        run(() => post(`/customers/${customer.id}/assign-plan`,
                       { plan_id: Number(planId), start_date: startDate }),
            "Plan assigned.");
      }}>
        <div className="dlg-grid">
          <DateField label="Plan start date" value={startDate} required
                     onChange={setStartDate} />
        </div>

        <PlanPicker value={planId} onChange={setPlanId} />

        {current && (
          <p className="hint">
            Assigning a new plan terminates <strong>{current.plan_name}</strong>,
            which currently runs to {fmtDate(current.end_date)}.
          </p>
        )}

        <DialogButtons busy={busy} onClose={onClose} disabled={!planId}
                       label={current ? "Change plan" : "Assign plan"} />
      </form>
    </Modal>
  );
}

/**
 * Renew (or change) the plan and raise the invoice for it.
 *
 * No money is taken here. Collecting at the same moment meant a mis-keyed
 * renewal arrived already settled and could not simply be withdrawn, and a
 * customer paying for a renewal AND an addon had to be entered twice. The bill
 * lands on the Pending Invoice tab, where one payment entry clears whatever
 * they are actually settling - with the discount spread across it all.
 */
/**
 * Renew: one decision, and a receipt for what it will do.
 *
 * This dialog used to be a form. Package (with a plan list behind a link),
 * cycles, start date, new expiry, amount, tax, remark - seven controls for an
 * operation whose whole meaning is "the same again". Every one of them was a
 * way to get a routine monthly renewal wrong: a stray keystroke in New expiry
 * moved a customer's service, an edited Amount silently rewrote the price
 * agreed with that customer, and picking a different package turned a renewal
 * into a plan change that closes the current plan record.
 *
 * What is left is the only thing that genuinely varies from one renewal to
 * the next: whether this bill carries GST. The plan, the dates and the price
 * are printed, not offered - the server derives them exactly as this panel
 * shows them, extending from the current expiry so paid-for days are not
 * lost. Changing the package has its own button on the Plan tab, and editing
 * the dates has its own dialog; neither belongs in the middle of taking
 * money.
 */
export function RenewPlanDialog({ customer, plan, onClose, onDone }) {
  const { busy, run, toast } = useSubmit({ onDone, onClose });

  const [quote, setQuote] = useState(null);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState({
    taxable: false, send_message: true, reactivate: false,
  });

  useEffect(() => {
    let cancelled = false;
    get(`/customers/${customer.id}/renew/quote`)
      .then((response) => {
        if (cancelled) return;
        const q = response?.data ?? response;
        setQuote(q);
        setForm((f) => ({
          ...f,
          // Opens on what this CUSTOMER is: tax_default is the company
          // setting narrowed by the customer's own Taxable / Non-Taxable
          // flag, so a non-taxable account is not quoted GST by accident.
          taxable: (q?.tax_default || 'notax') !== 'notax',
          reactivate: q?.customer?.is_active === false,
        }));
      })
      .catch(() => { if (!cancelled) setQuote(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [customer.id]);

  const active = quote?.active_plan;
  const gst = Number(quote?.gst_percent || 0);
  /* HOW a taxable renewal is expressed - GST added on top, or already inside
     the plan price - is a company setting, not a per-renewal choice, so the
     operator picks only taxed or not. */
  const taxableMode = quote?.tax_default && quote.tax_default !== "notax"
    ? quote.tax_default : "exclude";
  const mode = form.taxable ? taxableMode : "notax";
  const { base, tax, total } = splitTax(active?.price, gst, mode);

  // The package row's own details, for the speed the plan list carries and
  // the quote's active_plan does not.
  const master = (quote?.plans || []).find(
    (p) => String(p.id) === String(active?.plan_id));

  function submit(event) {
    event.preventDefault();
    run(async () => {
      /* Only the tax choice is sent. No dates, no amount, no plan id: the
         server then uses its own defaults - this plan, one cycle, extending
         from the current expiry, at the price agreed with this customer -
         which is the same arithmetic printed above the button. Sending an
         amount would also overwrite that agreed price. */
      const response = await post(`/customers/${customer.id}/renew`, {
        periods: 1,
        tax_applicable: mode,
        send_message: form.send_message,
        reactivate: form.reactivate,
      });
      const payload = response?.data ?? response;
      if (payload.message_status === "dry-run") {
        toast.warning("The renewal message was logged but NOT sent - the "
          + "messaging gateway is not configured.", { duration: 10000 });
      }
      return payload;
    }, (payload) => `Renewed to ${fmtDate(payload?.end_date)}. `
      + `Invoice ${payload?.invoice_no} for ${inr(payload?.grand_total)} is `
      + "waiting on Pending Invoice.");
  }

  if (loading) {
    return <Modal title="Renew plan" onClose={onClose} width={760}>
      <p className="hint">Loading the customer's plan…</p>
    </Modal>;
  }

  // Nothing to renew is not an error, it is a different job - say which one.
  if (!active) {
    return (
      <Modal title="Renew plan" onClose={onClose} width={760}>
        <p className="hint">
          This customer has no active plan, so there is nothing to renew.
          Use <strong>Assign/Change</strong> on the Plan tab to put them on
          a package.
        </p>
        <DialogButtons busy={false} onClose={onClose} label="" closeLabel="Close" />
      </Modal>
    );
  }

  return (
    <Modal title="Renew plan" onClose={onClose} width={760}>
      <form onSubmit={submit} className="renew-lite">
        <div className="renew-tax">
          <span className="renew-tax-label">Tax type</span>
          <div className="renew-tax-choices">
            <label>
              <input type="radio" name="renew-tax" checked={form.taxable}
                     onChange={() => setForm((f) => ({ ...f, taxable: true }))} />
              <span>Taxable</span>
            </label>
            <label>
              <input type="radio" name="renew-tax" checked={!form.taxable}
                     onChange={() => setForm((f) => ({ ...f, taxable: false }))} />
              <span>Non-taxable</span>
            </label>
          </div>
        </div>

        <div className="renew-sums">
          <span>Base amount <strong>{inr(base)}</strong></span>
          <span>GST {form.taxable ? `${gst}%` : ""} <strong>{inr(tax)}</strong></span>
          <span className="is-total">Total <strong>{inr(total)}</strong></span>
        </div>

        <ScrollArrows wrapClassName="table-wrap">
          <table className="data cards-sm renew-plan-table">
            <thead>
              <tr>
                <th>Package name</th><th>Type</th><th className="num">Price</th>
                <th>Start date</th><th>End date</th>
                <th className="num">Days rem.</th><th>Status</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td data-label="Package name">{active.plan_name}</td>
                <td data-label="Type">
                  {master?.speed_mbps ? `${master.speed_mbps} Mbps` : "—"}
                </td>
                <td className="num" data-label="Price">{inr(active.price)}</td>
                <td data-label="Start date">{fmtDate(active.start_date)}</td>
                <td data-label="End date">{fmtDate(active.end_date)}</td>
                <td className="num" data-label="Days rem.">
                  {Number(active.days_left ?? 0)}
                </td>
                <td data-label="Status">
                  <span className={`pill ${active.days_left >= 0 ? "ok" : "danger"}`}>
                    {active.days_left >= 0 ? "Active" : "Expired"}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </ScrollArrows>

        {/* Read-only, and the one fact the operator is committing to. */}
        <p className="renew-note">
          Renewing extends this plan to{" "}
          <strong>{fmtDate(quote?.suggested?.end_date)}</strong> and raises the
          invoice. Collect the money on the Pending Invoice tab
          {quote?.outstanding > 0
            ? `, where this account already owes ${inr(quote.outstanding)}.`
            : "."}
        </p>

        {quote?.customer?.is_active === false && (
          <label className="dlg-check">
            <input type="checkbox" checked={form.reactivate}
                   onChange={(e) => setForm((f) => ({ ...f, reactivate: e.target.checked }))} />
            <span>Reconnect this customer — their line is currently disabled</span>
          </label>
        )}

        <label className="dlg-check">
          <input type="checkbox" checked={form.send_message}
                 onChange={(e) => setForm((f) => ({ ...f, send_message: e.target.checked }))} />
          <span>WhatsApp the customer that their plan has been renewed</span>
        </label>

        <DialogButtons busy={busy} onClose={onClose} label={`Renew ${inr(total)}`} />
      </form>
    </Modal>
  );
}

/**
 * Split a price into base, GST and total the way the server will.
 *
 * `exclude` adds the rate on top; `include` means the price already contains
 * it and the base is worked backwards, which is how a 500 plan reads as 423.73
 * plus 76.27. Anything else is not taxed and the three figures collapse to one.
 */
function splitTax(amount, gstPercent, mode) {
  const value = Number(amount || 0);
  const rate = Number(gstPercent || 0);
  if (!value || rate <= 0 || mode === "notax") {
    return { base: value, tax: 0, total: value };
  }
  if (mode === "include") {
    const baseValue = value / (1 + rate / 100);
    return { base: baseValue, tax: value - baseValue, total: value };
  }
  const taxValue = value * rate / 100;
  return { base: value, tax: taxValue, total: value + taxValue };
}

export function EditPlanDialog({ plan, onClose, onDone }) {
  const { busy, run } = useSubmit({ onDone, onClose });
  const [form, setForm] = useState({
    total_price: rupees(plan?.price_monthly),
    start_date: plan?.start_date || "",
    end_date: plan?.end_date || "",
  });

  const invalidRange = form.start_date && form.end_date
    && form.end_date < form.start_date;

  return (
    <Modal title="Edit customer plan" onClose={onClose}>
      <form onSubmit={(event) => {
        event.preventDefault();
        run(() => put(`/customer-plans/${plan.id}`, form), (payload) =>
          payload?.repriced_invoices?.length
            ? `Saved. Repriced ${payload.repriced_invoices.join(", ")}.`
            : "Plan updated.");
      }}>
        <div className="dlg-grid">
          <label>
            <span>Total Price</span>
            <MoneyInput value={form.total_price}
                        onChange={(e) => setForm({ ...form, total_price: e.target.value })} />
          </label>
          <DateField label="Start date" value={form.start_date} required
                     onChange={(v) => setForm({ ...form, start_date: v })} />
          <DateField label="End date" value={form.end_date} required
                     onChange={(v) => setForm({ ...form, end_date: v })} />
        </div>
        <p className="hint">
          The price belongs to the plan, which other customers are also on, so
          changing it here reprices only <em>this</em> customer’s unpaid
          invoices. Invoices already paid are left exactly as issued.
        </p>
        {invalidRange && <p className="field-error">The end date is before the start date.</p>}
        <DialogButtons busy={busy} onClose={onClose} disabled={invalidRange} />
      </form>
    </Modal>
  );
}

export function ResetPasswordResult({ password, onClose }) {
  return (
    <Modal title="Temporary password" onClose={onClose}>
      <div className="cd-temp-password">
        <span>Read this out to the customer</span>
        <code>{password}</code>
        <small>
          It is shown once and is not stored in plain text. An email was sent
          as well.
        </small>
      </div>
      <DialogButtons onClose={onClose} label={null} closeLabel="Done" />
    </Modal>
  );
}

function DialogButtons({ busy, onClose, disabled, label = "Save", closeLabel = "Cancel" }) {
  return (
    <div className="dlg-buttons">
      <button type="button" className="btn" onClick={onClose} disabled={busy}>
        {closeLabel}
      </button>
      {label && (
        <button type="submit" className="btn primary" disabled={busy || disabled}>
          {busy ? "Working…" : label}
        </button>
      )}
    </div>
  );
}

/** Exposed so the detail page can clear a discount without its own handler. */
export const clearDiscount = (customerId) => del(`/customers/${customerId}/discount`);
