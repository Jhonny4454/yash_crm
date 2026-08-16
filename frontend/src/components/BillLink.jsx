import { useCallback, useState } from "react";
import api from "../api/client";
import { useToast } from "../context/ToastContext";

/**
 * Opening a bill, from anywhere a bill is listed.
 *
 * Clicking a bill ASKS what to do with it rather than deciding. Straight to a
 * new tab meant a customer who wanted a paper copy landed in a PDF viewer and
 * had to find its print control, and one who wanted the file got a tab instead
 * of a download - each of them one step away from what they actually pressed
 * the row for. Two named buttons cost one tap and remove the guess.
 *
 * The PDF route is token-protected, so a plain <a href> arrives signed out and
 * 401s. Everything here fetches with the auth header and works from the
 * resulting blob, which is also what makes a real print possible: the blob
 * goes into a hidden iframe and that iframe is printed, so the customer gets
 * the system print dialog rather than a viewer's toolbar.
 */

async function fetchBill(id) {
  const response = await api.get(`/portal/invoices/${id}/pdf`,
                                 { responseType: "blob" });
  return URL.createObjectURL(response.data);
}

/** Print without navigating away: the blob renders in a hidden frame. */
function printBlob(url) {
  const frame = document.createElement("iframe");
  frame.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0";
  frame.src = url;
  frame.onload = () => {
    try {
      frame.contentWindow.focus();
      frame.contentWindow.print();
    } catch {
      // Some browsers refuse to print a cross-document frame. A tab is the
      // honest fallback - the viewer there has its own print button.
      window.open(url, "_blank", "noopener");
    }
    // Long enough for the print dialog to have taken what it needs.
    setTimeout(() => frame.remove(), 60_000);
  };
  document.body.appendChild(frame);
}

function downloadBlob(url, filename) {
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

export function useBillActions() {
  const { toast } = useToast();
  const [pending, setPending] = useState(null);   // the invoice being asked about
  const [working, setWorking] = useState("");     // "print" | "download" | ""

  const ask = useCallback((invoice) => {
    if (invoice?.id) setPending(invoice);
  }, []);
  const dismiss = useCallback(() => {
    setPending(null);
    setWorking("");
  }, []);

  const run = useCallback(async (mode) => {
    if (!pending || working) return;
    setWorking(mode);
    try {
      const url = await fetchBill(pending.id);
      if (mode === "print") printBlob(url);
      else downloadBlob(url, `${pending.invoice_no || `invoice-${pending.id}`}.pdf`);
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
      dismiss();
    } catch {
      setWorking("");
      toast.error("That bill could not be opened. Please try again shortly.");
    }
  }, [dismiss, pending, toast, working]);

  return { pending, working, ask, dismiss, run };
}

/** The Print / Download sheet. Render once per screen. */
export function BillActions({ pending, working, dismiss, run }) {
  if (!pending) return null;

  return (
    <div className="bill-sheet-scrim" onClick={dismiss} role="presentation">
      <div className="bill-sheet" role="dialog" aria-modal="true"
           aria-label={`Bill ${pending.invoice_no || ""}`.trim()}
           onClick={(event) => event.stopPropagation()}>
        <h2>Bill {pending.invoice_no || ""}</h2>
        <p>What would you like to do with it?</p>

        <div className="bill-sheet-actions">
          <button type="button" className="btn primary" disabled={Boolean(working)}
                  onClick={() => run("print")}>
            {working === "print" ? "Preparing…" : "Print"}
          </button>
          <button type="button" className="btn" disabled={Boolean(working)}
                  onClick={() => run("download")}>
            {working === "download" ? "Preparing…" : "Download PDF"}
          </button>
        </div>

        <button type="button" className="btn ghost bill-sheet-cancel"
                onClick={dismiss}>Cancel</button>
      </div>
    </div>
  );
}

/**
 * Row-level props that make a whole row ask about its bill.
 *
 * A real button to assistive technology and to the keyboard, not a div with a
 * click handler - a row reachable only by mouse is not clickable "on all
 * devices".
 *
 * `extraClasses` exists because these props include a className of their own,
 * and a caller writing `<div className="my-row" {...billRowProps(...)}>` has
 * that className silently replaced. The row goes on working and loses every
 * one of its styles - which is how the bill list ended up with no layout, no
 * dividers and amounts hanging off the edge of the panel. Pass the row's own
 * classes here and they are merged instead of lost.
 */
export function billRowProps(invoice, ask, extraClasses = "") {
  return {
    className: `is-billrow${extraClasses ? ` ${extraClasses}` : ""}`,
    role: "button",
    tabIndex: 0,
    title: `Bill ${invoice.invoice_no || ""}`.trim(),
    onClick: (event) => {
      // A tap on the Pay button inside the row must not also open the sheet.
      if (event.target.closest("button, a, input, select")) return;
      ask(invoice);
    },
    onKeyDown: (event) => {
      if (event.target !== event.currentTarget) return;
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        ask(invoice);
      }
    },
  };
}
