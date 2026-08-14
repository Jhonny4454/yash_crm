import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import api, { post } from "../../api/client";
import { useToast } from "../../context/ToastContext";
import MoneyInput from "../MoneyInput";
import { inr, readableError } from "../ui";
import { Modal } from "./CustomerOptions";

/**
 * Open a token-protected PDF.
 *
 * The API guards /invoices/:id/pdf with the same bearer token as everything
 * else, so pointing window.open() at the URL sends an unauthenticated request
 * and the operator gets a 401 page instead of their bill. Fetching it as a
 * blob keeps the Authorization header on the request and hands the browser a
 * local object URL to render.
 *
 * The object URL is revoked on a timer rather than immediately: revoking it
 * straight away races the new tab and shows a blank viewer.
 */
export async function openProtectedPdf(path, { print = false } = {}) {
  const response = await api.get(path, { responseType: "blob" });
  const url = URL.createObjectURL(response.data);

  if (print) {
    // Print the bill itself, not the screen. A hidden frame keeps the
    // customer's copy identical to the PDF they would be sent, instead of
    // producing a printout of a table with the sidebar down one side.
    const frame = document.createElement("iframe");
    frame.style.position = "fixed";
    frame.style.right = "0";
    frame.style.bottom = "0";
    frame.style.width = "0";
    frame.style.height = "0";
    frame.style.border = "0";
    frame.src = url;
    frame.onload = () => {
      try {
        frame.contentWindow.focus();
        frame.contentWindow.print();
      } catch {
        // Some browsers refuse to print a cross-origin-ish blob frame; open
        // it instead so the click still leads somewhere.
        window.open(url, "_blank", "noopener");
      }
    };
    document.body.appendChild(frame);
    setTimeout(() => { frame.remove(); URL.revokeObjectURL(url); }, 60_000);
    return url;
  }

  const opened = window.open(url, "_blank", "noopener");
  if (!opened) {
    // Pop-up blocked. Fall back to a download so the click is not silently lost.
    const link = document.createElement("a");
    link.href = url;
    link.download = path.split("/").filter(Boolean).slice(-2).join("-") + ".pdf";
    link.click();
  }
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

/**
 * Icon rail used on the invoice and payment tables: PDF, WhatsApp, and (for
 * invoices) email. Matches the icon column in the live CRM.
 */
export function InvoiceActions({ invoice, compact = false, only = null }) {
  const { toast } = useToast();
  const [busy, setBusy] = useState(null);

  async function run(key, fn) {
    if (busy) return;
    setBusy(key);
    try {
      await fn();
    } catch (error) {
      toast.error(
        error.message === "no_mobile_number"
          ? "This customer has no mobile number on file."
          : error.detail || readableError(error),
      );
    } finally {
      setBusy(null);
    }
  }

  const openPdf = () => run("pdf", () => openProtectedPdf(`/invoices/${invoice.id}/pdf`));

  // The detailed bill adds the payment breakdown - mode, bank reference,
  // transaction and receipt numbers - which is what a customer asks for when
  // they are tracing a payment.
  const openDetailed = () => run("detail",
    () => openProtectedPdf(`/invoices/${invoice.id}/pdf?detail=1`));

  // Print opens the same PDF the customer would receive and asks the browser
  // to print it, rather than printing the screen - a screenshot of a table is
  // not a bill.
  const printBill = () => run("print", async () => {
    const url = await openProtectedPdf(`/invoices/${invoice.id}/pdf`, { print: true });
    return url;
  });

  const send = (channel) => run(channel, async () => {
    const response = await post(`/invoices/${invoice.id}/send`, { channel });
    const result = response?.data ?? response;
    if (result?.status === "dry-run") toast.warning(result.detail);
    else toast.success(`${invoice.invoice_no} sent to ${result?.to}.`);
  });

  const show = (key) => !only || only.includes(key);

  return (
    <div className={`doc-actions${compact ? " compact" : ""}`}>
      {show("pdf") && <IconButton icon="fa-file-pdf" short="PDF" label="Summary bill (PDF)"
                  busy={busy === "pdf"} onClick={openPdf} />}
      {show("detail") && <IconButton icon="fa-file-invoice" short="DTL"
                  label="Detailed bill, with payments"
                  busy={busy === "detail"} onClick={openDetailed} />}
      {show("print") && <IconButton icon="fa-print" short="PRN" label="Print this bill"
                  busy={busy === "print"} onClick={printBill} />}
      {show("email") && <IconButton icon="fa-envelope" short="EML" label="Email this bill"
                  tone="mail" busy={busy === "email"} onClick={() => send("email")} />}
      {show("whatsapp") && <IconButton icon="fa-whatsapp" short="WA" brand
                  label="WhatsApp this bill" tone="whatsapp"
                  busy={busy === "whatsapp"} onClick={() => send("whatsapp")} />}
    </div>
  );
}

/** Same rail for a payment: receipt PDF, a WhatsApp acknowledgement, and the
 * two ways money goes back out. */
export function ReceiptActions({ payment, onChanged }) {
  const { toast } = useToast();
  const [busy, setBusy] = useState(null);
  const [dialog, setDialog] = useState(null);
  const [open, setOpen] = useState(false);
  const [at, setAt] = useState(null);
  const wrapper = useRef(null);
  const menu = useRef(null);

  /* The menu is drawn into document.body, not into this row.
   *
   * A z-index cannot lift anything out of a clipping ancestor, and this rail
   * sits inside a rounded card and a horizontally scrollable table - both of
   * which clip. Dropped in place, the menu was cut in half by the bottom edge
   * of the card and "Cancel payment entry" was unreadable. A portal is the
   * only thing that escapes an ancestor's overflow. */
  useLayoutEffect(() => {
    if (!open) { setAt(null); return; }
    const button = wrapper.current?.querySelector(".rcp-more-btn");
    if (!button) return;
    const box = button.getBoundingClientRect();
    const width = 208;                       // matches .rcp-menu min-width
    const height = 86;                       // two items plus padding
    // Flip upwards near the bottom of the window, and never let the right
    // edge run off screen on a narrow one.
    const below = window.innerHeight - box.bottom > height + 8;
    setAt({
      top: below ? box.bottom + 4 : box.top - height - 4,
      left: Math.max(8, Math.min(box.right - width, window.innerWidth - width - 8)),
      width,
    });
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const close = (event) => {
      // Both, because the menu is no longer inside the wrapper. Missing the
      // menu here would close it on mousedown and swallow the click that
      // follows - the item would look dead.
      if (wrapper.current?.contains(event.target)) return;
      if (menu.current?.contains(event.target)) return;
      setOpen(false);
    };
    const esc = (event) => event.key === "Escape" && setOpen(false);
    // Anything that scrolls moves the row out from under a fixed menu, so
    // close rather than leave it hanging over unrelated rows. Capture, since
    // the scroll may happen in the table rather than the window.
    const scrolled = () => setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    window.addEventListener("scroll", scrolled, true);
    window.addEventListener("resize", scrolled);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
      window.removeEventListener("scroll", scrolled, true);
      window.removeEventListener("resize", scrolled);
    };
  }, [open]);

  async function run(key, fn) {
    if (busy) return;
    setBusy(key);
    try {
      await fn();
    } catch (error) {
      toast.error(error.detail || readableError(error));
    } finally {
      setBusy(null);
    }
  }

  // A refund only makes sense against money that actually counted, and a
  // negative row is itself a refund - returning one would be nonsense.
  const isRefund = Number(payment.amount) < 0;
  const cancelled = payment.status === "rejected";
  const canReturn = payment.status === "approved" && !isRefund;

  return (
    <div className="doc-actions" ref={wrapper}>
      <IconButton icon="fa-receipt" short="RCP" label="Open the receipt"
                  busy={busy === "pdf"}
                  onClick={() => run("pdf", () => openProtectedPdf(`/payments/${payment.id}/receipt`))} />
      <IconButton icon="fa-whatsapp" short="WA" brand label="WhatsApp the receipt" tone="whatsapp"
                  busy={busy === "send"}
                  onClick={() => run("send", async () => {
                    const response = await post(`/payments/${payment.id}/send`, {});
                    const result = response?.data ?? response;
                    if (result?.status === "dry-run") toast.warning(result.detail);
                    else toast.success(`Receipt sent to ${result?.to}.`);
                  })} />

      {/* The two destructive ones live behind a menu rather than sitting in
          the row. Reversing money is not a click you want next to "open the
          receipt" - a mis-aimed press should not be able to un-bank a
          payment. */}
      <div className="rcp-more">
        <button type="button" className="icon-btn rcp-more-btn"
                aria-haspopup="menu" aria-expanded={open}
                aria-label="More actions"
                title="More actions" disabled={cancelled && !isRefund}
                onClick={() => setOpen((v) => !v)}>
          <span aria-hidden="true">⋯</span>
        </button>

        {open && at && createPortal(
          <ul className="rcp-menu" role="menu" ref={menu}
              style={{ top: at.top, left: at.left, width: at.width }}>
            <li role="none">
              <button type="button" role="menuitem" disabled={!canReturn}
                      title={canReturn ? undefined
                        : isRefund ? "This entry is already a return."
                          : "Only an approved payment can be returned."}
                      onClick={() => { setOpen(false); setDialog("return"); }}>
                Sales return
              </button>
            </li>
            <li role="none">
              <button type="button" role="menuitem" className="danger"
                      disabled={cancelled}
                      title={cancelled ? "Already cancelled." : undefined}
                      onClick={() => { setOpen(false); setDialog("cancel"); }}>
                Cancel payment entry
              </button>
            </li>
          </ul>,
          document.body,
        )}
      </div>

      {dialog && (
        <ReversalDialog kind={dialog} payment={payment}
                        onClose={() => setDialog(null)}
                        onDone={async (message) => {
                          setDialog(null);
                          toast.success(message);
                          await onChanged?.();
                        }} />
      )}
    </div>
  );
}

/**
 * One square button in the icon rail.
 *
 * Two details that are easy to lose:
 *
 * `short` is not decoration. Font Awesome is loaded from a CDN and sometimes
 * is not there - a blocked request, a cold office connection, a corporate
 * proxy. Without a fallback every button in the rail becomes an identical
 * empty square and the operator has to click one to find out what it does.
 * The glyph is painted over the text (see .icon-fallback in Shared.css), so
 * when the font arrives nothing changes, and when it does not you get "PDF".
 *
 * `brand` picks the font family. WhatsApp lives in Font Awesome's brands set
 * (`fab`), everything else in solid (`fas`); using the wrong one renders a
 * blank, which is exactly the failure the fallback exists to catch, so it
 * would go unnoticed.
 */
function IconButton({ icon, short, label, busy, onClick, brand = false, tone }) {
  return (
    <button
      type="button"
      className={`icon-btn${tone ? ` ${tone}` : ""}`}
      // Both, deliberately: title gives the mouse a tooltip, aria-label gives
      // a screen reader something to say. The button has no text of its own.
      title={label}
      aria-label={label}
      disabled={!!busy}
      onClick={onClick}
    >
      {busy
        ? <span className="spinner tiny" aria-hidden="true" />
        : (
          <>
            <i className={`${brand ? "fab" : "fas"} ${icon}`} aria-hidden="true" />
            <span className="icon-fallback" aria-hidden="true">{short}</span>
          </>
        )}
    </button>
  );
}

/**
 * Ask for the amount and the reason before money moves back.
 *
 * The reason is required by the API, not merely encouraged. A cancelled
 * receipt with no explanation is a hole in the ledger that somebody has to
 * reconstruct months later from memory.
 *
 * Built on the app's own <Modal>, not on hand-rolled markup. The first
 * version used `.card-head` / `.card-body`, which belong to the page cards
 * rather than to dialogs - and `.card-body` is `padding: 20px 0`, no side
 * padding at all, so every line of text ran into the border of the box and
 * the footer buttons sat flush against each other. Reusing the real modal
 * gets the blue header, the padded body, Escape-to-close and the focus
 * handling for free, and this dialog now looks like every other one here.
 */
function ReversalDialog({ kind, payment, onClose, onDone }) {
  const returning = kind === "return";
  const full = Math.abs(Math.round(Number(payment.amount) || 0));
  const [amount, setAmount] = useState(String(full));
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const value = Number(amount);
  const valid = reason.trim().length > 0
    && (!returning || (Number.isFinite(value) && value > 0 && value <= full));

  async function submit(event) {
    event.preventDefault();
    if (!valid || busy) return;
    setBusy(true);
    setError(null);
    try {
      const url = returning
        ? `/payments/${payment.id}/return`
        : `/payments/${payment.id}/cancel`;
      const response = await post(url, returning
        ? { amount: value, reason: reason.trim() }
        : { reason: reason.trim() });
      const data = response?.data ?? response;
      onDone(data?.detail || (returning ? "Return recorded." : "Entry cancelled."));
    } catch (err) {
      /* A 404 here does not mean the receipt is gone - the row is on screen.
       * It means this Flask process was started before the reversal endpoints
       * existed, which is the one failure a plain "not found" explains
       * worst. Say what to do about it. */
      setError(err.status === 404
        ? "This server does not have the reversal endpoints yet. Restart "
          + "Flask (stop it and run python app.py again) so it picks up the "
          + "updated backend, then try once more."
        : err.detail || readableError(err));
      setBusy(false);
    }
  }

  return (
    <Modal title={returning ? "Sales return" : "Cancel payment entry"}
           onClose={onClose} width="30rem">
      <form className="rcp-dialog" onSubmit={submit}>
        {error && <div className="alert error" role="alert">{error}</div>}

        <p className="hint">
          {returning
            ? `Receipt R${payment.id} for ${inr(full)}. The receipt stays on `
              + "the ledger and the return is recorded beside it, so both "
              + "halves are visible."
            : `Receipt R${payment.id} for ${inr(full)} stops counting, and `
              + "any invoice it settled is owed again. Use this only when "
              + "the entry should never have existed — for money that is "
              + "genuinely going back, use Sales return."}
        </p>

        {returning && (
          <label className="dlg-block">
            <span>Amount to return</span>
            <MoneyInput value={amount} max={full} autoFocus
                        onChange={(e) => setAmount(e.target.value)} />
            {value > 0 && value < full && (
              <em className="hint">
                A part return. {inr(full - value)} of this receipt stays.
              </em>
            )}
          </label>
        )}

        <label className="dlg-block">
          <span>Reason</span>
          <textarea rows={3} required autoFocus={!returning} value={reason}
                    placeholder={returning
                      ? "Why is the money going back?"
                      : "Why should this entry not exist?"}
                    onChange={(e) => setReason(e.target.value)} />
          <em className="hint">Kept on the record and shown in the ledger.</em>
        </label>

        {/* .dlg-buttons, the same rail every other dialog here ends with:
            right-aligned, spaced, with a rule above it. */}
        <div className="dlg-buttons">
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            Close
          </button>
          <button type="submit" className={`btn ${returning ? "primary" : "danger"}`}
                  disabled={!valid || busy}>
            {busy ? "Working…"
              : returning ? `Return ${inr(value || 0)}` : "Cancel this entry"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
