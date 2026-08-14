/** Menu tree and page titles, shared by Sidebar and AdminLayout. */

export const MENU = [
  { name: "Dashboard", path: "/", icon: "fa-tachometer-alt", end: true },

  {
    name: "Customers", icon: "fa-users",
    children: [
      { name: "Add Customer", path: "/customers/add" },
      { name: "View Customers", path: "/customers" },
      { name: "Plan Status", path: "/customers/plan-status" },
      { name: "Customer Ledger", path: "/customers/ledger" },
      { name: "Generate Invoice", path: "/customers/generate-invoice" },
    ],
  },

  {
    name: "Plan Master", icon: "fa-wifi",
    children: [
      { name: "Service Providers", path: "/plan-master/service-providers" },
      { name: "Plans", path: "/plans" },
    ],
  },

  {
    name: "Invoices", icon: "fa-file-invoice",
    children: [
      { name: "All Invoices", path: "/invoices" },
      { name: "Payments", path: "/payments" },
      { name: "Authorising Report", path: "/authorizations" },
      { name: "Renewal Requests", path: "/renewals" },
    ],
  },

  {
    name: "Expenses", icon: "fa-money-bill-wave",
    children: [
      { name: "Expenses", path: "/expenses" },
      { name: "Categories", path: "/expenses/categories" },
      { name: "Accounts", path: "/expenses/accounts" },
      { name: "Payees", path: "/expenses/payees" },
    ],
  },

  {
    name: "Staff", icon: "fa-user-tie",
    children: [
      { name: "Staff Type", path: "/staff/types" },
      { name: "Staff", path: "/staff" },
    ],
  },

  {
    name: "Masters", icon: "fa-network-wired",
    children: [
      {
        name: "Address", children: [
          { name: "Building", path: "/masters/buildings" },
          { name: "Locality", path: "/masters/localities" },
          { name: "Area", path: "/masters/areas" },
        ],
      },
      {
        name: "Company", children: [
          { name: "Company Details", path: "/companies" },
          { name: "Zones", path: "/masters/zones" },
          { name: "Tax Master", path: "/masters/tax" },
          { name: "Addon Master", path: "/masters/addon-categories" },
          { name: "Discount Master", path: "/masters/discount-reasons" },
          // ✅ Updated paths to nest under Masters
          { name: "Database Backup", path: "/masters/backup" },
          { name: "Import / Export", path: "/masters/import-export" },
          { name: "ISP Integrations", path: "/masters/isp" },
        ],
      },
      { name: "Notifications", path: "/notifications" },
      { name: "Message Templates", path: "/masters/message-templates" },
      { name: "Bulk Messages", path: "/masters/bulk-messages" },
    ],
  },

  {
    name: "HR & Payroll", icon: "fa-user-clock",
    children: [
      { name: "Attendance", path: "/hr/attendance" },
      { name: "Leaves", path: "/hr/leaves" },
      { name: "Payroll", path: "/hr/payroll" },
    ],
  },

  {
    name: "Reports", icon: "fa-chart-bar",
    children: [
      { name: "Customer Expiry", path: "/reports/plan-expiry" },
      { name: "Attendance Report", path: "/reports/attendance" },
      { name: "Leaves Report", path: "/reports/leaves" },
      { name: "Salary Report", path: "/reports/payroll" },
    ],
  },

  { name: "Settings", path: "/settings", icon: "fa-cog" },
];


// ✅ Expanded to cover all routes defined in your App.jsx + MENU
export const TITLES = {
  "/": "Dashboard",
  "/authorizations": "Authorising Report",
  "/renewals": "Renewal Requests",
  "/companies": "Company Details",
  "/customers": "Customers",
  "/expenses": "Expenses",
  "/invoices": "Invoices",
  "/notifications": "Notifications",
  "/payments": "Payments",
  "/plans": "Plans",
  "/profile": "My Profile",
  "/settings": "Settings",
  "/staff": "Staff",
  "/customers/add": "Add Customer",
  "/customers/ledger": "Customer Ledger",
  "/customers/generate-invoice": "Generate Invoice",
  "/customers/plan-status": "Customer Plan Status",
  "/expenses/accounts": "Expense Accounts",
  "/expenses/categories": "Expense Categories",
  "/expenses/payees": "Expense Payees",
  "/hr/attendance": "Attendance",
  "/hr/leaves": "Leaves",
  "/hr/payroll": "Payroll",
  "/inventory/products": "Products",
  "/inventory/stock": "Stock",
  "/inventory/vendor-bills": "Vendor Bills",
  "/inventory/vendors": "Vendors",
  "/masters/addon-categories": "Addon Categories",
  "/masters/addresses": "Addresses",
  "/masters/discount-reasons": "Discount Master",
  "/masters/areas": "Areas",
  "/masters/backup": "Database Backup",
  "/masters/buildings": "Buildings",
  "/masters/bulk-messages": "Bulk Messages",
  "/masters/import-export": "Import / Export",
  "/masters/isp": "ISP Integrations",
  "/masters/localities": "Localities",
  "/masters/message-templates": "Message Templates",
  "/masters/tax": "Tax Master",
  "/masters/zones": "Zones",
  "/plan-master/service-providers": "Service Providers",
  "/reports/attendance": "Attendance Report",
  "/reports/collection": "Collection Report",
  "/reports/expenses": "Expense Report",
  "/reports/leaves": "Leaves Report",
  "/reports/payroll": "Salary Report",
  "/reports/plan-expiry": "Customer Expiry Report",
  "/staff/types": "Staff Types",
};