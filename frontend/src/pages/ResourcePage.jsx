import { Link } from "react-router-dom";
import CrudPage from "../components/CrudPage";

const yesNo = { key: "is_active", label: "Active", type: "checkbox" };

/* Fixed sets of values, spelled exactly as the database column stores them.
 *
 * These were free text boxes, which is a quiet way to break a record: an
 * Expense saved with a status of "Approved" or "aproved" is not the
 * "approved" every query filters on, so it simply stops appearing. Enum
 * columns are worse - the value is rejected or truncated on write, and on
 * SQLite it is accepted and then poisons every later read of that table.
 *
 * Anything listed here must match the db.Enum in models.py. `hr/attendance`
 * below already offered leave and holiday, which the column did NOT accept;
 * the column was widened to match rather than the choices being taken away,
 * because both are real things an office marks. */
const STAFF_ROLES = ["admin", "support", "field", "accounts"];
const APPROVAL_STATUSES = ["draft", "pending", "approved", "rejected"];
const BILL_STATUSES = ["draft", "pending", "partial", "paid", "cancelled"];
const ATTENDANCE_STATUSES = ["present", "absent", "half-day", "leave", "holiday"];
const CONNECTION_TYPES = ["Ethernet", "FTTH", "Lease Line"];
/* plan_type is a plain string column, so this is a convention rather than a
 * constraint - but a typed-in "Prepaid " with a trailing space still reads as
 * a different plan type everywhere it is grouped or filtered. */
const PLAN_TYPES = ["Prepaid", "Postpaid"];

export const RESOURCES = {
  customers: {
    title: "Customers", singular: "Customer", endpoint: "/customers",
    hint: "Create, update, deactivate and search customer accounts.",
    detailPrefix: "/customers/",
    columns: [
      { key: "first_name", label: "First name", required: true },
      { key: "last_name", label: "Last name", required: true },
      { key: "mobile", label: "Mobile", type: "tel", required: true },
      { key: "email", label: "Email", type: "email" }, { key: "username", label: "Portal username" },
      { key: "password", label: "Portal password", type: "password", hideInTable: true },
      { key: "zone", label: "Zone", type: "lookup", lookup: "/masters/zones", valueKey: "name", labelKey: "name" }, { key: "connection_type", label: "Connection", options: CONNECTION_TYPES },
      { key: "reference_id", label: "Reference ID" }, { key: "billing_address", label: "Billing address", type: "textarea", hideInTable: true },
      yesNo,
    ],
  },
  plans: {
    title: "Plans", singular: "Plan", endpoint: "/plans",
    columns: [
      { key: "name", label: "Plan name", required: true }, { key: "plan_code", label: "Code" },
      { key: "plan_type", label: "Type", options: PLAN_TYPES }, { key: "speed_mbps", label: "Speed (Mbps)", type: "number", required: true },
      { key: "price_monthly", label: "Monthly price", type: "money", required: true },
      { key: "validity_days", label: "Validity days", type: "number" },
      { key: "isp_amount", label: "ISP cost", type: "money", hideInTable: true },
      { key: "service_provider_id", label: "Service provider", type: "lookup", lookup: "/service-providers" }, yesNo,
    ],
  },
  companies: {
    title: "Company details", singular: "Company", endpoint: "/companies",
    columns: [
      { key: "name", label: "Company name", required: true }, { key: "mobile", label: "Mobile", type: "tel" },
      { key: "email", label: "Email", type: "email" }, { key: "gstin", label: "GSTIN" },
      { key: "website_url", label: "Website" }, { key: "address", label: "Address", type: "textarea" },
      { key: "bank_account_details", label: "Bank details", type: "textarea", hideInTable: true },
    ],
  },
  staff: {
    title: "Staff", singular: "Staff member", endpoint: "/staff",
    columns: [
      { key: "username", label: "Username", required: true }, { key: "full_name", label: "Full name" },
      { key: "password", label: "Password", type: "password", hideInTable: true }, { key: "email", label: "Email", type: "email" },
      { key: "mobile", label: "Mobile", type: "tel" }, { key: "role", label: "Role", options: STAFF_ROLES },
      { key: "staff_type_id", label: "Staff type", type: "lookup", lookup: "/staff/types" }, { key: "monthly_salary", label: "Monthly salary", type: "money" }, yesNo,
      // What this account is allowed to do. Hidden from the table because it
      // is a list, and a column of fifteen comma-separated keys tells nobody
      // anything; the Edit dialog is where it is read and set.
      { key: "permissions", label: "What this account can do", type: "permissions", hideInTable: true },
    ],
    hint: "Add staff, set their role, and choose exactly which parts of the system each one can use.",
  },
  "service-providers": {
    title: "Service providers", singular: "Service provider", endpoint: "/service-providers",
    columns: [{ key: "name", label: "Name", required: true }, yesNo],
  },
  "masters/zones": { title: "Zones", singular: "Zone", endpoint: "/masters/zones", columns: [
    { key: "name", label: "Name", required: true }, { key: "code", label: "Code" }, { key: "city", label: "City" }, { key: "state", label: "State" }, { key: "phone", label: "Phone", type: "tel" }, { key: "email", label: "Email", type: "email" }, { key: "address", label: "Address", type: "textarea" },
  ] },
  "masters/localities": { title: "Localities", singular: "Locality", endpoint: "/masters/localities", columns: [{ key: "name", label: "Name", required: true }] },
  "masters/areas": { title: "Areas", singular: "Area", endpoint: "/masters/areas", columns: [{ key: "name", label: "Name", required: true }] },
  "masters/buildings": { title: "Buildings", singular: "Building", endpoint: "/masters/buildings", columns: [{ key: "name", label: "Name", required: true }] },
  "masters/addresses": { title: "Addresses", singular: "Address", endpoint: "/masters/addresses", columns: [{ key: "name", label: "Name" }, { key: "city", label: "City" }, { key: "address", label: "Address", type: "textarea" }] },
  "masters/tax": { title: "Tax master", singular: "Tax entry", endpoint: "/masters/tax", columns: [{ key: "name", label: "Name", required: true }, { key: "value", label: "Rate", type: "number", suffix: "%", required: true, min: 0, max: 100 }] },
  // `template_type` is the stable key the application looks a template up by
  // - 'bill', 'renewal', 'due_reminder' - NOT a transport. The old options
  // here were sms/whatsapp/email/reminder, so anything created through this
  // screen got a type no code ever asks for and was never sent.
  //
  // The meta_* fields map a row to a template approved in Meta's WhatsApp
  // Manager, which is the only form WhatsApp carries to a customer who has
  // not messaged you in the last 24 hours - i.e. everyone a bill goes to.
  "masters/message-templates": { title: "Message templates", singular: "Template", endpoint: "/masters/message-templates", hint: "Used for WhatsApp and SMS. Placeholders like {{customer_name}} are filled in per customer. The Meta fields are only needed for WhatsApp outside the 24-hour window.", columns: [{ key: "name", label: "Name", required: true }, { key: "template_type", label: "Type", required: true, options: ["bill", "summary_bill", "detailed_bill", "renewal", "due_reminder", "payment_received", "payment_approved", "welcome", "payment_submitted", "payment_rejected", "renewal_approved", "expiry_3d", "expiry_2d", "expired"] }, { key: "body", label: "Message body", type: "textarea", required: true }, { key: "meta_template_name", label: "Meta template name", hideInTable: true }, { key: "meta_language", label: "Meta language", hideInTable: true }, { key: "meta_variables", label: "Meta variables (in order)", hideInTable: true }, { key: "is_active", label: "Active", type: "checkbox" }] },
  "masters/addon-categories": { title: "Addon categories", singular: "Addon category", endpoint: "/masters/addon-categories", columns: [{ key: "name", label: "Name", required: true }, { key: "description", label: "Description", type: "textarea" }] },
  "masters/discount-reasons": {
    title: "Discount master", singular: "Discount reason",
    endpoint: "/masters/discount-reasons",
    hint: "The reasons an operator may knock money off an addon invoice. Every discount on the ledger carries one of these, so a short bill can always be explained.",
    columns: [
      { key: "name", label: "Reason", required: true },
      { key: "default_amount", label: "Default amount", type: "money" },
      { key: "default_percent", label: "Default %", type: "number", suffix: "%", min: 0, max: 100 },
      { key: "description", label: "Description", type: "textarea" },
      yesNo,
    ],
  },
  "staff/types": { title: "Staff types", singular: "Staff type", endpoint: "/staff/types", columns: [{ key: "name", label: "Name", required: true }] },
  "expenses/categories": { title: "Expense categories", singular: "Expense category", endpoint: "/expenses/categories", columns: [{ key: "name", label: "Name", required: true }] },
  "expenses/accounts": { title: "Expense accounts", singular: "Expense account", endpoint: "/expenses/accounts", columns: [{ key: "name", label: "Name", required: true }] },
  "expenses/payees": { title: "Expense payees", singular: "Payee", endpoint: "/expenses/payees", columns: [{ key: "name", label: "Name", required: true }, { key: "mobile", label: "Mobile", type: "tel" }, { key: "email", label: "Email", type: "email" }, { key: "address", label: "Address", type: "textarea" }] },
  expenses: { title: "Expenses", singular: "Expense", endpoint: "/expenses", columns: [
    { key: "amount", label: "Amount", type: "money", required: true }, { key: "expense_date", label: "Date", type: "date" }, { key: "status", label: "Status", options: APPROVAL_STATUSES }, { key: "category_id", label: "Category", type: "lookup", lookup: "/expenses/categories", required: true }, { key: "account_id", label: "Paid from account", type: "lookup", lookup: "/expenses/accounts" }, { key: "payee_id", label: "Payee", type: "lookup", lookup: "/expenses/payees" }, { key: "description", label: "Description", type: "textarea" },
  ] },
  "inventory/vendors": { title: "Vendors", singular: "Vendor", endpoint: "/inventory/vendors", columns: [{ key: "name", label: "Name", required: true }, { key: "contact_person", label: "Contact person" }, { key: "mobile", label: "Mobile", type: "tel" }, { key: "email", label: "Email", type: "email" }, { key: "gstin", label: "GSTIN" }, { key: "address", label: "Address", type: "textarea" }, yesNo] },
  "inventory/products": { title: "Products", singular: "Product", endpoint: "/inventory/products", columns: [{ key: "name", label: "Name", required: true }, { key: "sku", label: "SKU" }, { key: "unit_price", label: "Selling price", type: "money" }, { key: "cost_price", label: "Cost price", type: "money" }, { key: "vendor_id", label: "Vendor", type: "lookup", lookup: "/inventory/vendors" }, { key: "tax_percent", label: "Tax", type: "number", suffix: "%" }, { key: "description", label: "Description", type: "textarea" }, yesNo] },
  "inventory/stock": { title: "Stock", singular: "Stock item", endpoint: "/inventory/stock", columns: [{ key: "product_id", label: "Product", type: "lookup", lookup: "/inventory/products", required: true }, { key: "quantity", label: "Quantity", type: "number", required: true }] },
  "inventory/vendor-bills": { title: "Vendor bills", singular: "Vendor bill", endpoint: "/inventory/vendor-bills", columns: [{ key: "bill_no", label: "Bill number", required: true }, { key: "vendor_id", label: "Vendor", type: "lookup", lookup: "/inventory/vendors", required: true }, { key: "bill_date", label: "Bill date", type: "date" }, { key: "due_date", label: "Due date", type: "date" }, { key: "total_amount", label: "Total", type: "money" }, { key: "status", label: "Status", options: BILL_STATUSES }, { key: "notes", label: "Notes", type: "textarea" }] },
  "hr/attendance": { title: "Attendance", singular: "Attendance entry", endpoint: "/hr/attendance", columns: [{ key: "user_id", label: "Staff member", type: "lookup", lookup: "/staff", labelKey: "full_name", required: true }, { key: "date", label: "Date", type: "date", required: true }, { key: "status", label: "Status", required: true, options: ATTENDANCE_STATUSES }] },
  "hr/leaves": { title: "Leave requests", singular: "Leave request", endpoint: "/hr/leaves", columns: [{ key: "user_id", label: "Staff member", type: "lookup", lookup: "/staff", labelKey: "full_name", required: true }, { key: "start_date", label: "Start date", type: "date", required: true }, { key: "end_date", label: "End date", type: "date", required: true }, { key: "status", label: "Status", options: ["pending", "approved", "rejected"] }, { key: "reason", label: "Reason", type: "textarea" }] },
  "hr/payroll": { title: "Payroll", singular: "Payroll entry", endpoint: "/hr/payroll", columns: [{ key: "user_id", label: "Staff member", type: "lookup", lookup: "/staff", labelKey: "full_name", required: true }, { key: "month_year", label: "Month", type: "date", required: true }, { key: "salary", label: "Salary", type: "money", required: true }, { key: "paid", label: "Paid", type: "checkbox" }] },
};

export default function ResourcePage({ resource }) {
  const config = RESOURCES[resource];
  if (!config) return <section className="page"><h1>Page unavailable</h1><p>This resource has not been configured.</p></section>;
  return <section className="page">
    <div className="page-heading"><div><h1>{config.title}</h1><p>{config.hint || `Manage ${config.title.toLowerCase()} from one place.`}</p></div>
      {config.detailPrefix && <Link className="btn" to="/customers/add">Open customer form</Link>}
    </div>
    <CrudPage {...config} />
  </section>;
}
