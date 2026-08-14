import { useEffect, useState } from "react";
import { get, post } from "../../api/client";
import { useToast } from "../../context/ToastContext";
import { inr, readableError } from "../ui";
import DateField from "../../components/DateField";
import MoneyInput from "../MoneyInput";

/**
 * Raise an extra charge on the account - installation, a shifting fee, a
 * replacement router.
 *
 * It raises the bill and stops there. Collecting in the same submit meant a
 * mis-keyed charge arrived already settled and could not be withdrawn, and a
 * customer paying for a renewal AND an addon had to be entered twice. The bill
 * lands in Pending Invoice, where one payment entry settles whatever they are
 * actually paying for.
 */
const BLANK = {
  amount: "",
  caption: "",
  discount_amount: "",
  discount_reason: "",
  invoice_date: new Date().toISOString().slice(0, 10),
  remark: "",
};

export default function AddonInvoice({ customer, onCancel, onDone }) {
  const { toast } = useToast();
  const [form, setForm] = useState(BLANK);
  const [reasons, setReasons] = useState([]);
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    get("/billing/options")
      .then((response) => setReasons((response?.data ?? response)?.discount_reasons || []))
      .catch(() => setReasons([]));
  }, []);

  const amount = Number(form.amount || 0);
  const discount = Number(form.discount_amount || 0);
  const net = Math.max(0, amount - discount);

  function set(key, value) {
    setForm((f) => ({ ...f, [key]: value }));
    setErrors((e) => ({ ...e, [key]: undefined }));
  }

  function validate() {
    const found = {};
    if (!(amount > 0)) found.amount = "Enter the amount to bill.";
    if (discount > amount) found.discount_amount = "More than the bill amount.";
    if (discount > 0 && !form.discount_reason) {
      found.discount_reason = "Pick a discount type.";
    }
    setErrors(found);
    return Object.keys(found).length === 0;
  }

  async function submit(event) {
    event.preventDefault();
    if (!validate() || busy) return;

    setBusy(true);
    try {
      const response = await post(`/customers/${customer.id}/addon-invoice`, {
        amount,
        caption: form.caption || undefined,
        discount_amount: discount || 0,
        discount_reason: form.discount_reason || undefined,
        invoice_date: form.invoice_date || undefined,
        remark: form.remark || undefined,
      });
      const invoice = (response?.data ?? response)?.invoice;
      toast.success(`${invoice?.invoice_no} raised for ${inr(net)}. `
        + "Take the payment from the list below when they pay.");
      setForm(BLANK);
      await onDone?.();
    } catch (error) {
      toast.error(error.detail || readableError(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="addon-form" onSubmit={submit}>
      <div className="addon-grid">
        <label>
          <span>Caption</span>
          <input value={form.caption} autoFocus placeholder="What is this for?"
                 onChange={(e) => set("caption", e.target.value)} />
        </label>
        <label>
          <span>Amount</span>
          <MoneyInput value={form.amount}
                      onChange={(e) => set("amount", e.target.value)} />
          {errors.amount && <small className="field-error">{errors.amount}</small>}
        </label>
        <label>
          <span>Discount type</span>
          <select value={form.discount_reason}
                  onChange={(e) => set("discount_reason", e.target.value)}>
            <option value="">-Discount Type-</option>
            {reasons.map((r) => <option key={r.id} value={r.name}>{r.name}</option>)}
          </select>
          {errors.discount_reason && (
            <small className="field-error">{errors.discount_reason}</small>
          )}
        </label>
        <label>
          <span>Discount</span>
          <MoneyInput value={form.discount_amount}
                      onChange={(e) => set("discount_amount", e.target.value)} />
          {errors.discount_amount && (
            <small className="field-error">{errors.discount_amount}</small>
          )}
        </label>
        <DateField label="Invoice date" value={form.invoice_date}
                   onChange={(v) => set("invoice_date", v)} />
        <label className="span-2">
          <span>Remark</span>
          <input value={form.remark} onChange={(e) => set("remark", e.target.value)} />
        </label>
      </div>

      <div className="addon-footer">
        <span className="addon-total">
          {discount > 0 && <>{inr(amount)} less {inr(discount)} — </>}
          <strong>{inr(net)}</strong> will be added to what this customer owes.
          No money is taken here.
        </span>
        <div className="row-actions">
          <button type="button" className="btn" onClick={onCancel}>Cancel</button>
          <button className="btn primary" disabled={busy}>
            {busy ? "Raising…" : "Raise invoice"}
          </button>
        </div>
      </div>
    </form>
  );
}
