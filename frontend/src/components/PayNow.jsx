import { useCallback, useEffect, useState } from "react";
import { get, post } from "../api/client";
import { useToast } from "../context/ToastContext";
import MoneyInput from "./MoneyInput";
import { inr, readableError } from "./ui";

/**
 * Paying, in one place.
 *
 * The checkout dance - raise an order, load the gateway SDK, open it, then
 * poll until the webhook has landed - was written once, inside the invoice
 * table. The customer's dashboard shows them a single number, "Outstanding",
 * and had no way to act on it: they had to go and find which bills that
 * number was made of and settle them one at a time. Copying the flow to a
 * second screen would mean two versions of the most consequential code in
 * the portal drifting apart, so it lives here and both screens call it.
 */

/**
 * Loads the gateway SDK once per page, no matter how many buttons are shown.
 *
 * Each button mounting its own <script> would fetch the SDK once per unpaid
 * invoice and race to define the same global.
 */
let sdkPromise = null;

/** Whether online payment is available, asked once per screen.
 *
 * Checked before a Pay control is drawn rather than after it is pressed:
 * offering Pay Now and then answering "not configured" wastes the
 * customer's time and teaches them the portal is broken. */
export function usePayConfig() {
  const [gateway, setGateway] = useState(null);
  useEffect(() => {
    get("/portal/pay/config")
      .then((response) => setGateway(response?.data ?? response))
      .catch(() => setGateway({ enabled: false, detail: "" }));
  }, []);
  return gateway;
}

export function loadCheckoutSdk(url) {
  if (window.Cashfree) return Promise.resolve(window.Cashfree);
  if (sdkPromise) return sdkPromise;

  sdkPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = url;
    script.async = true;
    script.onload = () => (window.Cashfree
      ? resolve(window.Cashfree)
      : reject(new Error("checkout_sdk_missing")));
    script.onerror = () => {
      sdkPromise = null;   // let a later attempt retry rather than fail forever
      reject(new Error("checkout_sdk_unreachable"));
    };
    document.head.appendChild(script);
  });
  return sdkPromise;
}

export function usePayNow({ gateway, onPaid }) {
  const { toast } = useToast();
  const [stage, setStage] = useState("idle");

  const confirmOrder = useCallback(async (orderId) => {
    // The webhook is authoritative and may land before or after the customer
    // returns, so poll rather than trusting whatever the redirect said.
    for (let attempt = 0; attempt < 10; attempt += 1) {
      // eslint-disable-next-line no-await-in-loop
      const response = await get(`/portal/pay/status/${orderId}`);
      const status = (response?.data ?? response)?.status;
      if (status === "paid") return "paid";
      if (status === "failed" || status === "expired") return status;
      // eslint-disable-next-line no-await-in-loop
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
    return "pending";
  }, []);

  /**
   * `invoiceId` may be omitted, which means "pay whatever is outstanding" -
   * the server spreads it across the open bills, oldest first.
   */
  const pay = useCallback(async ({ invoiceId, amount, describe }) => {
    if (stage !== "idle") return;
    setStage("starting");

    let order;
    try {
      const response = await post("/portal/pay/order", {
        ...(invoiceId ? { invoice_id: invoiceId } : {}),
        amount: Number(amount),
        return_url: window.location.href,
      });
      order = response?.data ?? response;
    } catch (orderError) {
      setStage("idle");
      toast.error(
        orderError.message === "payment_gateway_not_configured"
          ? "Online payment is not available right now. Please contact the office."
          : orderError.detail || readableError(orderError),
      );
      return;
    }

    try {
      const Cashfree = await loadCheckoutSdk(order.sdk_url || gateway?.sdk_url);
      const checkout = Cashfree({
        mode: (order.environment || gateway?.environment) === "production"
          ? "production" : "sandbox",
      });

      setStage("checkout");
      await checkout.checkout({
        paymentSessionId: order.payment_session_id,
        redirectTarget: "_modal",
      });
    } catch (sdkError) {
      setStage("idle");
      // The money may still have gone through in a redirect flow, so never
      // tell the customer it failed - tell them we are checking.
      if (String(sdkError?.message || "").startsWith("checkout_sdk")) {
        toast.error(
          "The payment window could not be opened. Check your connection and try again.",
        );
        return;
      }
      toast.warning("Checking whether that payment went through…");
    }

    setStage("confirming");
    try {
      const result = await confirmOrder(order.order_id);
      if (result === "paid") {
        toast.success(`Payment received${describe ? ` for ${describe}` : ""}. Thank you.`);
        await onPaid?.();
      } else if (result === "pending") {
        toast.warning(
          "We have not had confirmation from the bank yet. If money has left "
          + "your account it will show here shortly - please do not pay twice.",
          { duration: 12000 },
        );
      } else {
        toast.error("That payment did not complete. Nothing has been charged.");
      }
    } catch {
      toast.warning(
        "We could not confirm the payment just now. Please refresh in a "
        + "moment before trying again.",
        { duration: 12000 },
      );
    } finally {
      setStage("idle");
    }
  }, [confirmOrder, gateway, onPaid, stage, toast]);

  return { stage, busy: stage !== "idle", pay };
}

const STAGE_LABEL = {
  starting: "Opening…",
  checkout: "Waiting for payment…",
  confirming: "Confirming…",
};

/** Settle one bill. */
export function PayButton({ invoice, gateway, onPaid }) {
  const { stage, busy, pay } = usePayNow({ gateway, onPaid });

  return (
    <button type="button" className="btn sm primary pay-btn" disabled={busy}
            onClick={() => pay({
              invoiceId: invoice.id,
              amount: Number(invoice.balance),
              describe: invoice.invoice_no,
            })}>
      {STAGE_LABEL[stage] || `Pay ${inr(invoice.balance)}`}
    </button>
  );
}

/**
 * Settle the account total, or part of it.
 *
 * The amount is editable because a customer who can only pay half this week
 * will otherwise pay nothing at all - and a part payment recorded against the
 * oldest bill is worth considerably more to the business than a customer who
 * closed the tab.
 */
export function PayDuesPanel({ outstanding, invoiceCount, gateway, onPaid }) {
  const due = Number(outstanding || 0);
  const [amount, setAmount] = useState("");
  const { stage, busy, pay } = usePayNow({ gateway, onPaid });

  if (due <= 0) return null;

  const entered = amount === "" ? due : Number(amount);
  const valid = Number.isFinite(entered) && entered > 0 && entered <= due + 0.01;
  const part = valid && entered < due - 0.01;

  return (
    <section className="panel pay-dues">
      <div className="pay-dues-head">
        <div>
          <h2>Pay your dues</h2>
          <p>
            {inr(due)} outstanding
            {invoiceCount > 1
              ? ` across ${invoiceCount} bills. Your payment is applied to the oldest first.`
              : " on your account."}
          </p>
        </div>
        <strong className="pay-dues-total">{inr(due)}</strong>
      </div>

      {!gateway?.enabled ? (
        /* No card gateway is not the same as no way to pay.
         *
         * This used to be one sentence ending "contact us for the bank
         * details", which leaves somebody holding their phone, wanting to
         * give us money, with nothing to act on. The bank details are on the
         * company record already - so show them. */
        <div className="pay-dues-offline">
          <p className="pay-dues-off">
            {gateway?.detail
              || "Card and UPI payment is not switched on yet. You can still "
                 + "pay by bank transfer or at the office."}
          </p>
          {gateway?.offline?.upi && (
            <p><strong>UPI:</strong> <span className="mono">{gateway.offline.upi}</span></p>
          )}
          {gateway?.offline?.bank_details && (
            <div>
              <strong>Bank transfer</strong>
              <pre className="pay-dues-bank">{gateway.offline.bank_details}</pre>
            </div>
          )}
          {gateway?.offline?.phone && (
            <p>
              <strong>Questions?</strong>{" "}
              <a href={`tel:${gateway.offline.phone}`}>{gateway.offline.phone}</a>
            </p>
          )}
          <p className="pay-dues-note">
            Quote your account name when you transfer, so we can match the
            payment to your bill.
          </p>
        </div>
      ) : (
        <div className="pay-dues-row">
          <label htmlFor="pay-amount">Amount to pay</label>
          <div className="pay-dues-input">
            <span aria-hidden="true">₹</span>
            {/* Whole rupees, no spinner: the bills are whole rupees, and a
                number field here would let the mouse wheel change the amount
                being paid while the customer scrolls the page. */}
            <MoneyInput id="pay-amount" className="input"
                        value={amount} placeholder={String(Math.round(due))}
                        disabled={busy}
                        onChange={(event) => setAmount(event.target.value)} />
          </div>
          <button type="button" className="btn primary" disabled={busy || !valid}
                  onClick={() => pay({ amount: entered, describe: "your account" })}>
            {STAGE_LABEL[stage] || `Pay ${inr(entered)}`}
          </button>
        </div>
      )}

      {gateway?.enabled && !valid && (
        <p className="pay-dues-warn" role="alert">
          Enter an amount between ₹1 and {inr(due)} — that is everything owed
          on the account today.
        </p>
      )}
      {gateway?.enabled && part && (
        <p className="pay-dues-note">
          A part payment. {inr(due - entered)} will still be outstanding
          afterwards.
        </p>
      )}
    </section>
  );
}
