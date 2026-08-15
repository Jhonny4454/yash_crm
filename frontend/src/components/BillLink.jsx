import { useState } from "react";
import api from "../api/client";
import { useToast } from "../context/ToastContext";

/**
 * Opening a bill, from anywhere a bill is listed.
 *
 * The PDF route is token-protected, so a plain <a href> arrives signed out and
 * 401s - which is why the only way to see a bill used to be a small "Bill"
 * button that fetched it with the auth header. That left every other place a
 * bill appears (the dashboard's recent list, the row itself) looking clickable
 * to nobody and doing nothing when tapped.
 *
 * So: fetch with the header, hand the browser a local blob, and open that. The
 * browser's own PDF viewer then provides print and download, which is what
 * people are actually reaching for - rather than us building two more buttons
 * that do worse versions of both.
 *
 * If the popup blocker eats the new tab, it falls back to a direct download
 * instead of silently doing nothing.
 */
export function useOpenBill() {
  const { toast } = useToast();
  const [openingId, setOpeningId] = useState(null);

  async function openBill(invoice) {
    const id = invoice?.id;
    if (!id || openingId) return;
    setOpeningId(id);

    let url;
    try {
      const response = await api.get(`/portal/invoices/${id}/pdf`,
                                     { responseType: "blob" });
      url = URL.createObjectURL(response.data);

      const tab = window.open(url, "_blank", "noopener");
      if (!tab) {
        const link = document.createElement("a");
        link.href = url;
        link.download = `${invoice.invoice_no || `invoice-${id}`}.pdf`;
        document.body.appendChild(link);
        link.click();
        link.remove();
      }
      // Long enough for the viewer to have finished with it.
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch {
      if (url) URL.revokeObjectURL(url);
      toast.error("That bill could not be opened. Please try again shortly.");
    } finally {
      setOpeningId(null);
    }
  }

  return { openingId, openBill };
}

/**
 * Row-level props that make a whole row open its bill.
 *
 * Spread onto a <tr> or an <article>. It is a real button to assistive
 * technology and to the keyboard, not a div with a click handler - a row you
 * can only reach with a mouse is not clickable "on all devices".
 */
export function billRowProps(invoice, openBill, openingId) {
  return {
    className: "is-billrow",
    role: "button",
    tabIndex: 0,
    "aria-busy": openingId === invoice.id || undefined,
    title: `Open bill ${invoice.invoice_no || ""}`.trim(),
    onClick: (event) => {
      // A tap on the Pay button inside the row must not also open the PDF.
      if (event.target.closest("button, a, input, select")) return;
      openBill(invoice);
    },
    onKeyDown: (event) => {
      if (event.target !== event.currentTarget) return;
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openBill(invoice);
      }
    },
  };
}
