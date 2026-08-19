import { useState } from "react";
import { useFetch } from "../api/useFetch";
import { BillActions, billRowProps, useBillActions } from "../components/BillLink";
import { PayButton, PayDuesPanel, usePayConfig, usePayee } from "../components/PayNow";
import { Empty, ErrorNote, fmtDate, inr, Loading, Pager } from "../components/ui";
import { useT } from "../context/LanguageContext";
import "../styles/PortalPay.css";

/**
 * The customer's bills, and what is still to pay on each.
 *
 * This was a seven-column table. On a phone it stacked into seven labelled
 * lines per bill, so one invoice filled half the screen and a customer with a
 * year of history scrolled past a dozen of them to reach anything; on a
 * desktop the same table was mostly white space, with the two things that
 * matter - which bill, and what is left on it - separated by four columns of
 * dates and repeated status words.
 *
 * It is one list now, and the same list at every width: bill number and dates
 * on the left, the money on the right, an action only where there is one to
 * take. A page of twelve bills fits on a phone screen, which for most
 * customers is their whole history. The row itself still opens the PDF.
 */
export default function PortalInvoices() {
  const [page, setPage] = useState(1);
  const { data, meta, loading, error, refetch } = useFetch("/portal/invoices", { page });
  const gateway = usePayConfig();
  const payee = usePayee(gateway);
  const bill = useBillActions();
  const t = useT();

  const rows = Array.isArray(data) ? data : [];
  const outstanding = Number(meta?.outstanding || 0);

  return (
    <section className="page">
      <div className="page-heading">
        <div>
          <h1>{t("bills.title")}</h1>
          <p>{t("bills.payable_to")} <strong>{payee}</strong></p>
        </div>
      </div>

      <ErrorNote error={error} onRetry={refetch} />

      <PayDuesPanel outstanding={outstanding} invoiceCount={meta?.due_invoice_count}
                    gateway={gateway} onPaid={refetch} />

      <section className="panel bill-panel">
        <div className="bill-panel-head">
          <h2>{t("bills.your_bills")}</h2>
          <span className="bill-panel-hint">{t("bills.tap_bill")}</span>
        </div>

        {loading ? <Loading label={t("bills.loading")} />
          : !rows.length ? <Empty title={t("bills.no_bills")}
                                  hint={t("bills.no_bills_hint")} />
            : (
              /* When any bill on the page can be paid, every row reserves the
                 button's width - otherwise the amounts on settled rows sit
                 further right than the ones on unpaid rows, and a column of
                 figures that does not line up is a column nobody can scan. */
              <div className={`bill-list${rows.some(canPay(gateway)) ? " has-pay" : ""}`}>
                {rows.map((invoice) => (
                  <BillRow key={invoice.id} invoice={invoice} gateway={gateway}
                           onPaid={refetch} ask={bill.ask} />
                ))}
              </div>
            )}
      </section>

      <Pager meta={meta} onPage={setPage} />

      <BillActions {...bill} />
    </section>
  );
}

/** Whether a bill still has money on it AND there is a way to pay it here. */
const canPay = (gateway) => (invoice) =>
  Number(invoice.balance || 0) > 0 && Boolean(gateway?.enabled);

/**
 * One bill, one row.
 *
 * The status is a coloured edge and one word under the amount rather than a
 * pill in a column of its own: it is the least-consulted thing on the row and
 * it was taking the most horizontal space.
 */
function BillRow({ invoice, gateway, onPaid, ask }) {
  const balance = Number(invoice.balance || 0);
  const unpaid = balance > 0;
  // Overdue earns its own colour. "You owe this" and "you owed this three
  // weeks ago" are different messages and only one of them is urgent.
  const overdue = unpaid && invoice.due_date
    && new Date(invoice.due_date) < new Date(new Date().toDateString());
  const tone = !unpaid ? "is-paid" : overdue ? "is-overdue" : "is-due";

  return (
    /* The row's classes go THROUGH billRowProps, not beside it: those props
       carry a className of their own, and a `{...spread}` after a className
       attribute silently replaces it. That is what stripped `bill-row` off
       every row - the layout, the padding and the dividers all vanished and
       the amounts fell out of the panel. */
    <div {...billRowProps(invoice, ask, `bill-row ${tone}`)}>
      <div className="bill-row-id">
        <strong className="bill-row-no">{invoice.invoice_no || `Bill #${invoice.id}`}</strong>
        <span className="bill-row-dates">
          {fmtDate(invoice.issue_date)}
          {invoice.due_date ? ` · due ${fmtDate(invoice.due_date)}` : ""}
        </span>
      </div>

      <div className="bill-row-money">
        <strong>{invoice.status === 'cancelled' ? '\u20b90' : inr(unpaid ? balance : invoice.total_amount)}</strong>
        <span>{invoice.status === 'cancelled' ? 'voided' : !unpaid ? "paid" : overdue ? "overdue" : "to pay"}</span>
      </div>

      {canPay(gateway)(invoice) && (
        <PayButton invoice={invoice} gateway={gateway} onPaid={onPaid} compact />
      )}
    </div>
  );
}
