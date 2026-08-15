import { useCallback, useEffect, useState } from "react";
import { get, post } from "../api/client";
import { useToast } from "../context/ToastContext";
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
 * Settle the account total. One button, for the whole figure.
 *
 * The amount used to be editable, on the reasoning that somebody who can only
 * pay half this week would otherwise pay nothing. In practice it handed the
 * customer a text box at the exact moment money moves - an invitation to a
 * typo, and a piece of arithmetic the server had already done and shown them
 * two lines above. Part payments are a conversation with the office, which is
 * a conversation the business wants to have anyway.
 */
export function PayDuesPanel({ outstanding, invoiceCount, gateway, onPaid }) {
  const due = Number(outstanding || 0);
  const { stage, busy, pay } = usePayNow({ gateway, onPaid });

  if (due <= 0) return null;

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
        /* One button, for the whole amount. The customer does not type a
           figure: an editable box invites a typo at the exact moment money
           moves, and it made the customer responsible for arithmetic the
           server had already done. Somebody who genuinely can only pay part
           of it rings the office, which is a conversation the business wants
           to have anyway. */
        <div className="pay-dues-row">
          <button type="button" className="btn primary" disabled={busy}
                  onClick={() => pay({ amount: due, describe: "your account" })}>
            {STAGE_LABEL[stage] || `Pay ${inr(due)}`}
          </button>
        </div>
      )}
    </section>
  );
}
