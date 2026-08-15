import { useEffect, useMemo, useRef, useState } from "react";
import { del, get, post, put } from "../../api/client";
import { useToast } from "../../context/ToastContext";
import { fmtDate, inr, rupees, readableError } from "../ui";
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
    { key: "sms", label: "Send SMS", admin: true },
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

export function SmsDialog({ customer, onClose, onDone }) {
  const { busy, run } = useSubmit({ onDone, onClose });
  const [message, setMessage] = useState("");

  return (
    <Modal title={`Send SMS to ${customer.mobile || "this customer"}`} onClose={onClose}>
      <form onSubmit={(event) => {
        event.preventDefault();
        run(() => post(`/customers/${customer.id}/send-sms`, { message }),
            "Message sent.");
      }}>
        <label className="dlg-block">
          <span>Message</span>
          <textarea rows={5} value={message} maxLength={640} required
                    onChange={(e) => setMessage(e.target.value)} />
        </label>
        <p className="hint">{message.length}/640 characters.</p>
        <DialogButtons busy={busy} onClose={onClose} disabled={!message.trim()}
                       label="Send" />
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
  const [startDate, setStartDate] = useState(new Date().toLocaleDateString("en-CA"));

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
export function RenewPlanDialog({ customer, plan, onClose, onDone }) {
  const { busy, run, toast } = useSubmit({ onDone, onClose });

  const [quote, setQuote] = useState(null);
  const [loading, setLoading] = useState(true);
  const [errors, setErrors] = useState({});
  /* Renew means "the same plan again". The package list stays out of the way
     until somebody asks for it: this dialog opened on a dropdown of every
     plan in the master, so the ordinary monthly renewal - by far the most
     common thing done here - started with a decision nobody wanted to make,
     and picking the wrong line silently turned a renewal into a plan change
     that closes the customer's current plan record. */
  const [picking, setPicking] = useState(false);
  const [form, setForm] = useState({
    plan_id: "", periods: 1, start_date: "", end_date: "",
    amount: "", tax_applicable: "notax", remarks: "",
    send_message: true, reactivate: false,
  });

  useEffect(() => {
    let cancelled = false;
    get(`/customers/${customer.id}/renew/quote`)
      .then((response) => {
        if (cancelled) return;
        const q = response?.data ?? response;
        setQuote(q);
        const current = q?.active_plan;
        setForm((f) => ({
          ...f,
          plan_id: String(current?.plan_id || ""),
          start_date: q?.suggested?.start_date || "",
          end_date: q?.suggested?.end_date || "",
          amount: rupees(current?.price),
          // Open on whatever the company is configured to do, not a hard-coded
          // "Non-taxable". The customer portal bills from the same setting, so
          // a default of notax here meant the same renewal cost less at the
          // counter than it did online.
          tax_applicable: q?.tax_default || "notax",
          reactivate: q?.customer?.is_active === false,
        }));
      })
      .catch(() => { if (!cancelled) setQuote(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [customer.id]);

  const plans = quote?.plans || [];
  const chosen = plans.find((p) => String(p.id) === String(form.plan_id));
  const isChange = Boolean(quote?.active_plan
    && String(quote.active_plan.plan_id) !== String(form.plan_id));
  // With no active plan there is nothing to renew, so the list is the only
  // way forward and hiding it would leave a dead dialog.
  const mustPick = !quote?.active_plan;
  const showPlanList = picking || mustPick;

  // Keep the dates and price in step with the plan and cycle count, so the
  // operator is never quoting from a figure that no longer applies.
  useEffect(() => {
    if (!chosen || !quote) return;
    const from = new Date(quote.suggested.extends_from);
    from.setDate(from.getDate()
      + (chosen.validity_days || 30) * Number(form.periods || 1));
    /* Renewing the plan they are already on charges the price agreed with
       THIS customer, not the master price - which is what the server does
       when no amount is sent, and what the locked plan card above shows. A
       customer on a 1000 plan at an agreed 800 was being shown "₹800 ·
       renewing the same plan" over a button that billed ₹1,000. */
    const samePlan = String(chosen.id) === String(quote.active_plan?.plan_id);
    const unit = samePlan
      ? Number(quote.active_plan.price ?? chosen.price_monthly ?? 0)
      : Number(chosen.price_monthly || 0);
    setForm((f) => ({
      ...f,
      end_date: from.toLocaleDateString("en-CA"),
      amount: rupees(unit * Number(form.periods || 1)),
    }));
  }, [form.plan_id, form.periods, chosen, quote]);

  const amount = Number(form.amount || 0);
  const gst = Number(quote?.gst_percent || 0);
  const tax = form.tax_applicable === "exclude" ? amount * gst / 100 : 0;
  const grandTotal = amount + tax;

  const set = (key) => (event) => {
    const value = event.target.type === "checkbox"
      ? event.target.checked : event.target.value;
    setForm((f) => ({ ...f, [key]: value }));
    setErrors((e) => ({ ...e, [key]: undefined }));
  };

  function submit(event) {
    event.preventDefault();
    const found = {};
    if (!form.plan_id) found.plan_id = "Choose a plan.";
    if (amount < 0) found.amount = "The amount cannot be negative.";
    if (form.end_date && form.start_date && form.end_date <= form.start_date) {
      found.end_date = "Must be after the start date.";
    }
    setErrors(found);
    if (Object.keys(found).length) return;

    run(async () => {
      const response = await post(`/customers/${customer.id}/renew`, {
        ...form,
        plan_id: Number(form.plan_id),
        periods: Number(form.periods) || 1,
        amount,
      });
      const payload = response?.data ?? response;
      // A message that was only logged must never read as one that arrived.
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
    return <Modal title="Renew plan" onClose={onClose} width={700}>
      <p className="hint">Loading the customer's plan…</p>
    </Modal>;
  }

  return (
    <Modal title={isChange ? "Change plan" : "Renew plan"}
           onClose={onClose} width={700}>
      <form onSubmit={submit} className="renew-form">
        <fieldset className="renew-block">
          <legend>Plan</legend>
          <div className="renew-grid">
            {showPlanList ? (
              <label>
                <span>Package</span>
                <select value={form.plan_id} onChange={set("plan_id")} autoFocus>
                  <option value="">-Select-</option>
                  {plans.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} — {inr(p.price_monthly)} / {p.validity_days}d
                      {String(p.id) === String(quote?.active_plan?.plan_id)
                        ? "  (current plan)" : ""}
                    </option>
                  ))}
                </select>
                {errors.plan_id && <small className="field-error">{errors.plan_id}</small>}
                {!mustPick && (
                  <button type="button" className="link-btn" onClick={() => {
                    setPicking(false);
                    setForm((f) => ({ ...f, plan_id: String(quote.active_plan.plan_id) }));
                  }}>Keep the current plan instead</button>
                )}
              </label>
            ) : (
              <label>
                <span>Package</span>
                <div className="renew-locked">
                  <strong>{quote.active_plan.plan_name}</strong>
                  <span>{inr(quote.active_plan.price)} · renewing the same plan</span>
                </div>
                <button type="button" className="link-btn"
                        onClick={() => setPicking(true)}>
                  Change to a different plan
                </button>
              </label>
            )}
            <label>
              <span>Cycles</span>
              <MoneyInput min="1" max="36" value={form.periods} onChange={set("periods")} />
            </label>
            <DateField label="Start date" value={form.start_date}
                       onChange={(v) => setForm((f) => ({ ...f, start_date: v }))} />
            <div>
              <DateField label="New expiry" value={form.end_date}
                         onChange={(v) => setForm((f) => ({ ...f, end_date: v }))} />
              {errors.end_date && <small className="field-error">{errors.end_date}</small>}
            </div>
          </div>
          {quote?.active_plan && (
            <p className="hint">
              Currently {quote.active_plan.plan_name}, expiring{" "}
              {fmtDate(quote.active_plan.end_date)}
              {quote.active_plan.days_left >= 0
                ? ` (${quote.active_plan.days_left} days left).`
                : ` (${Math.abs(quote.active_plan.days_left)} days ago).`}
              {" "}The new period runs from {fmtDate(quote.suggested.extends_from)},
              so days already paid for are not lost.
              {isChange && " Changing the plan closes the current one and starts a new record."}
            </p>
          )}
        </fieldset>

        <fieldset className="renew-block">
          <legend>Charge</legend>
          <div className="renew-grid">
            <label>
              <span>Amount</span>
              <MoneyInput value={form.amount} onChange={set("amount")} />
              {errors.amount && <small className="field-error">{errors.amount}</small>}
            </label>
            <label>
              <span>Tax</span>
              <select value={form.tax_applicable} onChange={set("tax_applicable")}>
                <option value="notax">Non-taxable</option>
                <option value="exclude">Add GST {gst}%</option>
                <option value="include">GST {gst}% included</option>
              </select>
            </label>
            <label className="span-2">
              <span>Remark</span>
              <input value={form.remarks} onChange={set("remarks")}
                     placeholder="Optional note on the invoice" />
            </label>
          </div>

          <div className="renew-total">
            <span>Amount {inr(amount)}</span>
            {tax > 0 && <span>plus GST {inr(tax)}</span>}
            <strong>Invoice {inr(grandTotal)}</strong>
          </div>
        </fieldset>

        <p className="renew-note">
          This raises the invoice only. Collect the money on the Pending
          Invoice tab, where it can be settled together with any addon charge
          {quote?.outstanding > 0
            ? ` — this account already owes ${inr(quote.outstanding)}.`
            : "."}
        </p>

        {quote?.customer?.is_active === false && (
          <label className="dlg-check">
            <input type="checkbox" checked={form.reactivate} onChange={set("reactivate")} />
            <span>Reconnect this customer — their line is currently disabled</span>
          </label>
        )}

        <label className="dlg-check">
          <input type="checkbox" checked={form.send_message} onChange={set("send_message")} />
          <span>WhatsApp the customer that their plan has been renewed</span>
        </label>

        <DialogButtons busy={busy} onClose={onClose}
                       label={`${isChange ? "Change plan" : "Renew"} and bill `
                              + `${inr(grandTotal)}`} />
      </form>
    </Modal>
  );
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
          It is shown once and is not stored in plain text. An SMS and email
          were attempted as well.
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
