import { useCallback, useEffect, useState } from "react";
import { get, post } from "../api/client";
import { useAuth } from "../context/AuthContext";
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

/**
 * Who the customer is paying, in their own words.
 *
 * The company name is already on the session (it is what the top bar and the
 * footer print), so this is a read, not a request. The gateway config carries
 * it too and is used as the fallback for a session saved before branding was
 * returned; the literal last resort matches the name the shell falls back to,
 * so the two can never disagree on screen.
 */
export function usePayee(gateway) {
  const { company } = useAuth();
  return (company?.name || gateway?.offline?.company || "").trim()
    || "Yash Internet Services";
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
      if (status === "failed" || status === "expired" || status === "cancelled") {
        return status;
      }
      // Still untouched after a couple of checks means the customer closed
      // the window without attempting to pay. Sitting on "Confirming…" for
      // the full twenty seconds after a decision they have already made is
      // twenty seconds of pretending we do not know.
      if (status === "created" && attempt >= 2) return "abandoned";
      // eslint-disable-next-line no-await-in-loop
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
    return "pending";
  }, []);

  /**
   * `invoiceId` may be omitted, which means "pay whatever is outstanding" -
   * the server spreads it across the open bills, oldest first.
   */
  const pay = useCallback(async ({ invoiceId, intent, amount, describe }) => {
    if (stage !== "idle") return;
    setStage("starting");

    let order;
    try {
      const response = await post("/portal/pay/order", {
        ...(invoiceId ? { invoice_id: invoiceId } : {}),
        // A renewal has no bill yet - `intent` says what the money is for and
        // the server prices it. Sending an amount from the browser for
        // something that does not exist yet would let the page name its own
        // price.
        ...(intent ? { intent } : {}),
        ...(amount !== undefined ? { amount: Number(amount) } : {}),
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
      return "order_failed";
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
        return "unopened";
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
      return result;
    } catch {
      toast.warning(
        "We could not confirm the payment just now. Please refresh in a "
        + "moment before trying again.",
        { duration: 12000 },
      );
      return "unknown";
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

/**
 * Settle one bill.
 *
 * `compact` is for a row that already prints the amount two centimetres to
 * the left: repeating it in the button costs the width of a phone screen and
 * tells the customer nothing they are not looking at.
 */
export function PayButton({ invoice, gateway, onPaid, compact = false }) {
  const { stage, busy, pay } = usePayNow({ gateway, onPaid });

  return (
    <button type="button"
            className={`btn sm primary pay-btn${compact ? " is-compact" : ""}`}
            disabled={busy}
            onClick={() => pay({
              invoiceId: invoice.id,
              amount: Number(invoice.balance),
              describe: invoice.invoice_no,
            })}>
      {STAGE_LABEL[stage] || (compact ? "Pay" : `Pay ${inr(invoice.balance)}`)}
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
  const payee = usePayee(gateway);

  if (due <= 0) return null;

  return (
    <section className="panel pay-dues">
      {/* The heading names the payee rather than the action. "Pay your dues"
          told the customer what they already knew from the amount above it;
          who the money is going to is the thing this screen alone can say,
          and the thing a customer hesitates over at a payment window that
          carries the gateway's branding instead of ours. */}
      <div className="pay-dues-head">
        <div className="pay-dues-lede">
          <h2>Pay {payee}</h2>
          {/* Two flex children rather than one string: the amount and the
              note wrap onto separate lines on a narrow phone, and a string
              would leave the separator stranded at the start of the second
              line. */}
          <p>
            <strong className="pay-dues-total">{inr(due)}</strong>
            <span>
              {invoiceCount > 1
                ? `${invoiceCount} bills, oldest cleared first`
                : "outstanding"}
            </span>
          </p>
        </div>

        {gateway?.enabled && (
          <button type="button" className="btn primary pay-dues-cta" disabled={busy}
                  onClick={() => pay({ amount: due, describe: "your account" })}>
            {STAGE_LABEL[stage] || `Pay ${inr(due)}`}
          </button>
        )}
      </div>

      {/* The Pay button lives in the head, beside the amount, so the panel is
          one band on a desktop instead of three stacked rows. Only the
          offline instructions need the space below it. */}
      {!gateway?.enabled && (
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
      )}
    </section>
  );
}
