import { useCallback, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { post } from "../api/client";
import { useFetch } from "../api/useFetch";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";
import {
  AssignPlanDialog, clearDiscount, DiscountDialog, DueReminderBell,
  EditPlanDialog, OptionsMenu, RenewPlanDialog, ResetMacDialog,
  ResetPasswordResult, SmsDialog,
} from "../components/customers/CustomerOptions";
import {
  CustomerLogTab, InvoiceHistoryTab, LedgerTab, MessageLogTab,
  OverviewTab, PaymentHistoryTab, PendingInvoiceTab, PlanHistoryTab, PlanTab,
  
} from "../components/customers/CustomerTabs";
import { ErrorNote, inr, Loading, readableError } from "../components/ui";
import "../styles/CustomerDetail.css";

/**
 * Customer workspace, laid out like the live CRM: an identity header with the
 * money chips, then one tab per thing you might have come here to do.
 *
 * The tab lives in the query string rather than component state so a link to
 * "this customer's payment ledger" is shareable and survives a refresh - an
 * operator on the phone to a customer should not have to re-navigate after
 * hitting reload.
 *
 * Ticketing is not here. It was removed from this build, so a Service Request
 * tab would open onto a feature that does not exist.
 */

const TABS = [
  { key: "overview", label: "Overview" },
  { key: "plan", label: "Plan" },
  { key: "pending", label: "Pending Invoice" },
  { key: "invoices", label: "Invoice History" },
  { key: "payments", label: "Payment History" },
  { key: "messages", label: "SMS Log" },
  { key: "plan-history", label: "Plan History" },
  { key: "logs", label: "Customer Log" },
  { key: "ledger", label: "Payment Ledger" },
];

export default function CustomerDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const auth = useAuth();
  const { toast, confirm } = useToast();
  const [search, setSearch] = useSearchParams();

  const { data, loading, error, refetch } = useFetch(`/customers/${id}`);
  const [dialog, setDialog] = useState(null);
  const [busyAction, setBusyAction] = useState(null);

  const tab = TABS.some((t) => t.key === search.get("tab"))
    ? search.get("tab") : "overview";

  const setTab = useCallback((key) => {
    const next = new URLSearchParams(search);
    if (key === "overview") next.delete("tab");
    else next.set("tab", key);
    setSearch(next, { replace: true });
  }, [search, setSearch]);

  const customer = data?.customer;
  const plans = useMemo(() => data?.plans || [], [data]);
  const invoices = useMemo(() => data?.invoices || [], [data]);
  const payments = useMemo(() => data?.payments || [], [data]);
  const outstanding = Number(data?.outstanding || 0);

  const activePlan = useMemo(
    () => plans.find((plan) => plan.status === "active") || plans[0] || null,
    [plans],
  );

  /** One place for the fire-and-confirm actions the Options menu triggers. */
  const runAction = useCallback(async (key) => {
    if (busyAction) return;

    const confirmations = {
      disable: {
        title: "Disable this customer?",
        message: "Their connection will be cut until the account is enabled again.",
        confirmLabel: "Disable",
        tone: "danger",
      },
      terminate: {
        title: "Terminate this customer?",
        message: "The account is deactivated and the active plan is terminated. "
          + "This is not the same as disabling - it closes the service.",
        confirmLabel: "Terminate",
        tone: "danger",
      },
      "clear-discount": {
        title: "Cancel the discount?",
        message: "Future bills will be raised at the full plan price.",
        confirmLabel: "Cancel discount",
      },
      "reset-password": {
        title: "Reset the portal password?",
        message: "A new temporary password is generated and texted to the customer. "
          + "Their current password stops working immediately.",
        confirmLabel: "Reset",
      },
    };

    if (confirmations[key] && !(await confirm(confirmations[key]))) return;

    setBusyAction(key);
    try {
      if (key === "enable" || key === "disable" || key === "terminate") {
        const response = await post(`/customers/${id}/${key}`, {});
        const payload = response?.data ?? response;
        toast.success(`Customer ${key}d.`);
        if (payload?.network_synced === false) {
          toast.warning("The record was updated, but the router did not confirm "
            + "the change. Check the line before telling the customer.");
        }
      } else if (key === "clear-discount") {
        await clearDiscount(id);
        toast.success("Discount cancelled.");
      } else if (key === "reset-password") {
        const response = await post(`/customers/${id}/reset-password`, {});
        const payload = response?.data ?? response;
        setDialog({ type: "password", password: payload?.temporary_password });
      } else if (key === "reminder") {
        const response = await post(`/customers/${id}/send-reminder`, {});
        const payload = response?.data ?? response;
        if (payload?.status === "dry-run") toast.warning(payload.detail);
        else {
          toast.success(`Reminder for ${inr(payload?.due_amount)} sent to ${payload?.to}.`);
        }
      }
      await refetch();
    } catch (actionError) {
      toast.error(
        actionError.message === "nothing_outstanding"
          ? "This customer has nothing outstanding, so there is no reminder to send."
          : actionError.detail || readableError(actionError),
      );
    } finally {
      setBusyAction(null);
    }
  }, [busyAction, confirm, id, refetch, toast]);

  function onOptionPick(key) {
    switch (key) {
      case "edit":
        return navigate(`/customers/${id}/edit`);
      case "addon":
        return setTab("pending");
      case "notes":
        return setTab("overview");
      case "ledger":
        return setTab("ledger");
      case "discount":
        return setDialog({ type: "discount" });
      case "sms":
        return setDialog({ type: "sms" });
      case "reset-mac":
        return setDialog({ type: "reset-mac" });
      default:
        return runAction(key);
    }
  }

  if (loading) return <Loading label="Loading customer" />;
  if (error) return <ErrorNote error={error} onRetry={refetch} />;
  if (!customer) return <ErrorNote error="not_found" />;

  // Always rupees. A percentage left over from before is converted for
  // display against the plan price, so the chip never mixes units.
  const discountLabel = inr(customer.discount_amount);

  return (
    <div className="customer-detail">
      <header className="cd-header">
        <div>
          <h1>
            {customer.full_name}
            {!customer.is_active && <span className="pill danger">Disabled</span>}
          </h1>
        </div>

        <div className="cd-header-right">
          <div className="cd-chips">
            <span className="cd-chip">Account Id <b>{customer.account_id}</b></span>
            <span className="cd-chip">Discount <b>{discountLabel}</b></span>
            <span className={`cd-chip${outstanding > 0 ? " is-due" : ""}`}>
              Due <b>{inr(outstanding)}</b>
            </span>
          </div>
          {/* Two ways in on purpose, and they run the same code: the bell
              for the operator who is already chasing money, the menu entry
              for the one working down the list of things they can do. */}
          <DueReminderBell customer={customer} outstanding={outstanding}
                           busy={busyAction === "reminder"}
                           onSend={() => runAction("reminder")} />
          <OptionsMenu customer={customer} isAdmin={auth.isAdmin}
                       outstanding={outstanding} onPick={onOptionPick} />
        </div>
      </header>

      {busyAction && (
        <div className="alert info cd-busy" role="status">
          <span className="spinner" /> Applying “{busyAction.replace(/-/g, " ")}”…
        </div>
      )}

      <nav className="cd-tabs" role="tablist" aria-label="Customer sections">
        {TABS.map((item) => (
          <button key={item.key} type="button" role="tab"
                  aria-selected={tab === item.key}
                  className={tab === item.key ? "is-active" : ""}
                  onClick={() => setTab(item.key)}>
            {item.label}
            {item.key === "pending" && (data?.pending_invoice_count > 0) && (
              <span className="tab-count">{data.pending_invoice_count}</span>
            )}
          </button>
        ))}
      </nav>

      <div className="cd-panel" role="tabpanel">
        {tab === "overview" && (
          <OverviewTab customer={customer} outstanding={outstanding} onRefresh={refetch} />
        )}
        {tab === "plan" && (
          <PlanTab customer={customer} plans={plans}
                   onAssign={(plan) => setDialog({ type: "assign", plan: plan || activePlan })}
                   onRenew={(plan) => setDialog({ type: "renew", plan })}
                   onEdit={(plan) => setDialog({ type: "edit-plan", plan })} />
        )}
        {tab === "pending" && (
          <PendingInvoiceTab customer={customer} onRefresh={refetch} />
        )}
        {tab === "invoices" && <InvoiceHistoryTab invoices={invoices} />}
        {tab === "payments" && <PaymentHistoryTab payments={payments} />}
        {tab === "messages" && <MessageLogTab customerId={id} />}
        {tab === "plan-history" && <PlanHistoryTab customerId={id} />}
        {tab === "logs" && <CustomerLogTab customerId={id} />}
        {tab === "ledger" && <LedgerTab customerId={id} />}
      </div>

      {dialog?.type === "discount" && (
        <DiscountDialog customer={customer} onDone={refetch}
                        onClose={() => setDialog(null)} />
      )}
      {dialog?.type === "sms" && (
        <SmsDialog customer={customer} onDone={refetch}
                   onClose={() => setDialog(null)} />
      )}
      {dialog?.type === "reset-mac" && (
        <ResetMacDialog customer={customer} onDone={refetch}
                        onClose={() => setDialog(null)} />
      )}
      {dialog?.type === "assign" && (
        <AssignPlanDialog customer={customer} current={dialog.plan} onDone={refetch}
                          onClose={() => setDialog(null)} />
      )}
      {dialog?.type === "renew" && (
        <RenewPlanDialog customer={customer} plan={dialog.plan || activePlan}
                         onDone={refetch} onClose={() => setDialog(null)} />
      )}
      {dialog?.type === "edit-plan" && (
        <EditPlanDialog plan={dialog.plan || activePlan} onDone={refetch}
                        onClose={() => setDialog(null)} />
      )}
      {dialog?.type === "password" && (
        <ResetPasswordResult password={dialog.password}
                             onClose={() => setDialog(null)} />
      )}
    </div>
  );
}
