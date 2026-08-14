/**
 * Tiny stand-in for the Flask API, just enough to render every screen.
 *
 * The real backend needs MySQL, which this sandbox does not have. What we
 * actually want to catch here is *frontend* runtime failures - a bad import,
 * a null dereference, a hook used outside its provider - so the API only has
 * to answer with the right envelope shape ({ ok, data, meta }) and plausible
 * fields. Anything the UI mis-handles will still blow up in the console.
 */
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// This file already lives in frontend/smoke. Going through `../frontend`
// looked for frontend/frontend/dist, so the route smoke server started but
// crashed as soon as it tried to serve the built application.
const DIST = path.resolve(fileURLToPath(new URL("../dist", import.meta.url)));

const MIME = {
  ".html": "text/html", ".js": "text/javascript", ".css": "text/css",
  ".jpg": "image/jpeg", ".png": "image/png", ".svg": "image/svg+xml",
  ".json": "application/json", ".map": "application/json",
};

const staffUser = {
  id: 1, username: "admin", full_name: "Admin User", email: "admin@yash.in",
  mobile: "9876500000", role: "admin", is_active: true,
};

const customerUser = {
  id: 7, first_name: "Ravi", last_name: "Kumar", full_name: "Ravi Kumar",
  mobile: "9876512345", email: "ravi@example.com", username: "ravi",
  reference_id: "YIS-0007", zone: "North", connection_type: "Fibre",
  primary_address: "Flat 3, Sunrise Apartments", is_active: true,
  flat_no: "3", building: "Sunrise Apartments", locality: "Kothrud", area: "Pune West",
  home_phone: "02012345678", gstin: "27ABCDE1234F1Z5", pan: "ABCDE1234F", tax_type: "GST",
  discount_percent: 10, discount_amount: 0, notes: "Prefers evening calls.",
};

const branding = { name: "YASH Internet Services", logo_url: null };

const named = (n, extra = {}) => ({ id: n, name: `Record ${n}`, is_active: true, ...extra });

const invoice = (n) => ({
  id: n, invoice_no: `INV-2608-0000${n}`, customer_id: 7, customer_name: "Ravi Kumar",
  issue_date: "2026-07-01", due_date: "2026-07-15", total_amount: 799 + n,
  net_amount: 799 + n, balance: n % 2 ? 799 + n : 0, tax_amount: 0,
  status: n % 3 === 0 ? "overdue" : n % 2 ? "sent" : "paid", caption: "Monthly plan",
});

const payment = (n) => ({
  id: n, customer_id: 7, customer_name: "Ravi Kumar", invoice_id: n, invoice_no: `INV-2608-0000${n}`,
  amount: 799, payment_date: "2026-07-05", payment_mode: "Cash", mode_group: "cash",
  source: "admin", source_label: "Admin", status: n % 2 ? "pending" : "approved",
  needs_authorization: Boolean(n % 2), receipt_no: `RCP-${n}`,
});

const meta = { page: 1, per_page: 25, total: 3, pages: 1, has_prev: false, has_next: false };

function payloadFor(pathname) {
  const list = (fn, count = 3) => Array.from({ length: count }, (_, i) => fn(i + 1));

  // --- auth ---
  if (pathname === "/auth/staff/me") return { user: staffUser, branding };
  if (pathname === "/auth/customer/me") return { customer: customerUser, branding, active_plan: null };

  // --- dashboards ---
  if (pathname === "/dashboard") {
    return {
      customers: { total: 128, active: 119, inactive: 9 },
      plans: { active: 117, expiring_7_days: 6, expired: 4 },
      money: {
        collected_this_month: 92400, outstanding: 18350, pending_authorization: 3,
        by_mode: { cash: 41000, cheque: 12000, online: 33400, other: 6000 },
      },
      trend: [
        { month: "Feb 2026", amount: 71000 }, { month: "Mar 2026", amount: 80500 },
        { month: "Apr 2026", amount: 76200 }, { month: "May 2026", amount: 88100 },
        { month: "Jun 2026", amount: 90300 }, { month: "Jul 2026", amount: 92400 },
      ],
    };
  }
  if (pathname === "/dashboard/summary") {
    const chips = (from, n) => Array.from({ length: n }, (_, i) => {
      const d = new Date("2026-08-05T00:00:00Z");
      d.setUTCDate(d.getUTCDate() + from + i);
      const iso = d.toISOString().slice(0, 10);
      return { date: iso, label: iso.slice(8) + " Aug", count: (i * 3) % 7 };
    });
    return {
      as_of: "2026-08-05",
      customers: { total: 128, active: 119, inactive: 9, new_this_month: 6 },
      plans: { active: 117, expired: 4, expiring: chips(0, 8), recently_expired: chips(-7, 7) },
      invoices: { total_bills: 118, total_amount: 94320, paid_bills: 96,
                  paid_amount: 76800, pending_bills: 22, pending_amount: 17520 },
      collections: {
        today: { cash: 2400, cheque: 0, online: 1600, other: 0, total: 4000 },
        this_month: { cash: 41000, cheque: 12000, online: 33400, other: 6000, total: 92400 },
        last_month: { cash: 38000, cheque: 9000, online: 31000, other: 4000, total: 82000 },
      },
      outstanding: 18350,
      pending_authorization: 3,
      trend: [{ month: "2026-07", label: "Jul 2026", amount: 92400 }],
    };
  }
  if (pathname === "/dashboard/zones") {
    return {
      outstanding: [
        { zone: "North", count: 9, amount: 8400 },
        { zone: "South", count: 6, amount: 5200 },
        { zone: "Unassigned", count: 5, amount: 4750 },
      ],
      collection: [
        { zone: "North", count: 31, amount: 41200 },
        { zone: "South", count: 24, amount: 33100 },
        { zone: "East", count: 14, amount: 18100 },
      ],
    };
  }
  if (pathname === "/dashboard/monthly") {
    return list((n) => ({
      month: `2026-0${n}`, label: ["Jul", "Jun", "May"][n - 1] + " 2026",
      new_clients: n * 2, total_bills: 110 + n, total_amount: 90000 + n * 1000,
      paid_bills: 95, paid_amount: 76000, pending_bills: 15 + n, pending_amount: 14000 + n * 1000,
    }));
  }
  if (/^\/customers\/\d+\/documents/.test(pathname)) {
    return { documents: [
      { slot: "reg_form", key: "reg_form_file", label: "Reg. Form", filename: "r.pdf",
        doc_type: "", type_field: "", url: "/static/uploads/kyc/r.pdf" },
      { slot: "address_proof", key: "address_proof_file", label: "Address Proof",
        filename: "", doc_type: "Aadhaar", type_field: "address_proof_type", url: null },
      { slot: "id_proof", key: "id_proof_file", label: "ID Proof", filename: "",
        doc_type: "", type_field: "id_proof_type", url: null },
      { slot: "photo", key: "photo_file", label: "Photo", filename: "p.jpg",
        doc_type: "", type_field: "", url: "/static/uploads/kyc/p.jpg" },
    ], rejected: [] };
  }

  if (/^\/customers\/\d+\/pending-invoices$/.test(pathname)) {
    return {
      invoices: [
        { id: 11, invoice_no: "IN4790", caption: "FR_100Mbps_30Days",
          invoice_type: "plan", issue_date: "2026-08-01", due_date: "2026-08-16",
          period_start: "2026-08-11", period_end: "2026-09-10",
          total_amount: 800, discount_amount: 0, paid_amount: 0, balance: 800,
          status: "sent", can_delete: true },
        { id: 12, invoice_no: "IN4791", caption: "Router replacement",
          invoice_type: "addon", issue_date: "2026-08-06", due_date: "2026-08-21",
          period_start: "", period_end: "",
          total_amount: 500, discount_amount: 0, paid_amount: 0, balance: 500,
          status: "sent", can_delete: true },
        { id: 13, invoice_no: "IN4788", caption: "Installation",
          invoice_type: "addon", issue_date: "2026-07-20", due_date: "2026-08-04",
          period_start: "", period_end: "",
          total_amount: 300, discount_amount: 0, paid_amount: 100, balance: 200,
          status: "overdue", can_delete: false },
      ],
      total_outstanding: 1500, count: 3,
      payment_modes: ["Cash", "Cheque", "Online Transfer", "Credit Card",
                      "Paytm", "GooglePay", "PhonePay", "Bank Transfer"],
      referenced_modes: ["Bank Transfer", "Cheque", "Credit Card", "GooglePay",
                         "Online Transfer", "Paytm", "PhonePay"],
      discount_reasons: [{ id: 1, name: "Power Supply" }, { id: 2, name: "wire supply" }],
    };
  }

  if (pathname === "/portal/pay/config") {
    // Enabled, so the smoke run actually renders the Pay button rather than
    // silently exercising the "gateway off" branch every time.
    return { enabled: true, gateway: "cashfree", environment: "sandbox",
             sdk_url: "https://sdk.cashfree.com/js/v3/cashfree.js", detail: "" };
  }
  if (/^\/portal\/pay\/status\//.test(pathname)) {
    return { order_id: "ord_1", status: "paid", amount: 799,
             transaction_id: "cf_123", payment_method: "upi", invoice: invoice(1) };
  }

  if (pathname === "/portal/dashboard") {
    return {
      customer: customerUser,
      active_plan: { plan_id: 1, plan_name: "Fibre 100", speed_mbps: 100,
                     end_date: "2026-09-01", days_left: 27 },
      outstanding: 799,
      recent_invoices: list(invoice, 2),
      recent_payments: list(payment, 2),
    };
  }

  // --- customer detail and its tabs ---
  const detail = pathname.match(/^\/customers\/(\d+)$/);
  if (detail) {
    return {
      customer: {
        ...customerUser,
        account_id: `C${detail[1]}`,
        billing_type: "Prepaid", wallet_balance: 250,
        ip_address: "10.0.4.21", ipacct_id: "L2S-4471",
        service_provider: "Record 1", latitude: "19.154300", longitude: "72.998600",
        discount_percent: 0, discount_amount: 100, notes: "Prefers evening calls.",
        documents: [
          { key: "reg_form_file", label: "Reg. Form", filename: "c1-reg_form-ab12.pdf", doc_type: "", url: "/static/uploads/kyc/x.pdf" },
          { key: "address_proof_file", label: "Address Proof", filename: "", doc_type: "Aadhaar Card", url: null },
          { key: "id_proof_file", label: "ID Proof", filename: "", doc_type: "", url: null },
          { key: "photo_file", label: "Photo", filename: "", doc_type: "", url: null },
        ],
      },
      plans: [
        { id: 1, plan_id: 1, plan_name: "Fibre 100", plan: { service_provider: "L2S" }, price_monthly: 799, start_date: "2026-06-01", end_date: "2026-09-01", days_left: 27, status: "active", auto_renew: true },
        { id: 2, plan_id: 2, plan_name: "Fibre 50", plan: { service_provider: "L2S" }, price_monthly: 499, start_date: "2026-01-01", end_date: "2026-05-31", days_left: -60, status: "terminated", auto_renew: false },
      ],
      invoices: list(invoice), payments: list(payment), outstanding: 799,
      pending_invoice_count: 2, wallet_balance: 250, invoice_count: 3,
    };
  }
  if (/^\/customers\/\d+\/wallet$/.test(pathname)) {
    return {
      balance: 250,
      entries: [
        { id: 1, amount: 250, balance_after: 250, kind: "credit", reason: "Overpayment on INV-2607-00001", invoice_no: "INV-2607-00001", created_by: "Staff Member 1", created_at: "2026-07-05T10:00:00" },
        { id: 2, amount: -100, balance_after: 150, kind: "debit", reason: "Applied to July bill", invoice_no: "", created_by: "Staff Member 1", created_at: "2026-07-06T10:00:00" },
      ],
    };
  }
  if (/^\/customers\/\d+\/messages$/.test(pathname)) {
    return list((n) => ({ id: n, phone: "9800000000", channel: "whatsapp", template_type: ["bill", "due_reminder", "renewal"][n - 1] || "bill", body: "Your bill of 799 is due on 15-08-2026.", status: n === 2 ? "failed" : "sent", error: n === 2 ? "Gateway timeout" : "", created_at: "2026-07-0" + n }));
  }
  if (/^\/customers\/\d+\/logs$/.test(pathname)) {
    return list((n) => ({ id: n, action: ["Renew Plan", "Addon Invoice", "Add Discount"][n - 1] || "Edit", details: "Renewed plan Fibre 100 until 01-09-2026", user: "Staff Member 1", ip_address: "10.0.0.5", created_at: "2026-07-0" + n }));
  }
  if (/^\/customers\/\d+\/inventory$/.test(pathname)) {
    return list((n) => ({ id: n, product_id: n, product: `ONT Model ${n}`, sku: `ONT-00${n}`, serial_number: `SN00000${n}`, assigned_date: "2026-06-0" + n, status: "Active" }), 2);
  }
  if (/^\/customers\/\d+\/plan-history$/.test(pathname)) {
    return [
      { id: 1, plan_name: "Fibre 100", plan: { service_provider: "L2S" }, price_monthly: 799, start_date: "2026-06-01", end_date: "2026-09-01", auto_renew: true, status: "active" },
      { id: 2, plan_name: "Fibre 50", plan: { service_provider: "L2S" }, price_monthly: 499, start_date: "2026-01-01", end_date: "2026-05-31", auto_renew: false, status: "terminated" },
    ];
  }
  if (/^\/customers\/\d+\/ledger$/.test(pathname)) {
    return {
      customer: { id: 1, full_name: "Ravi Kumar", account_id: "C1", mobile: "9876512345", zone: "North", username: "ravi" },
      entries: [
        { type: "invoice", date: "2026-07-01", reference: "INV-2607-00001", description: "Fibre 100", debit: 799, credit: 0, balance: 799, invoice_id: 1, payment_id: null },
        { type: "wallet", date: "2026-06-06", reference: "W1", description: "Overpayment on INV-2606-00001", debit: 250, credit: 0, balance: 250, invoice_id: 2, payment_id: 1 },
        { type: "payment", date: "2026-06-05", reference: "R1", description: "Cash", debit: 0, credit: 1049, balance: 0, invoice_id: 2, payment_id: 1 },
        { type: "invoice", date: "2026-06-01", reference: "INV-2606-00001", description: "Fibre 100", debit: 799, credit: 0, balance: 799, invoice_id: 2, payment_id: null },
      ],
      closing_balance: 799,
      total_debit: 1848,
      total_credit: 1049,
      wallet_balance: 250,
    };
  }
  if (/^\/customers\/\d+\/renew\/quote$/.test(pathname)) {
    return {
      customer: { id: 1, full_name: "Ravi Kumar", account_id: "C1", username: "ravi",
                  mobile: "9876512345", is_active: true, wallet_balance: 250,
                  discount_percent: 0, discount_amount: 0 },
      active_plan: { customer_plan_id: 1, plan_id: 1, plan_name: "Fibre 100 Mbps",
                     price: 800, start_date: "2026-07-12", end_date: "2026-08-11",
                     auto_renew: true, days_left: 3 },
      plans: [
        { id: 1, name: "FR_100Mbps_30Days", price_monthly: 800, speed_mbps: 100, validity_days: 30 },
        { id: 2, name: "FR_100Mbps_365Days", price_monthly: 4800, speed_mbps: 100, validity_days: 365 },
        { id: 3, name: "FR_200Mbps_30Days", price_monthly: 1400, speed_mbps: 200, validity_days: 30 },
      ],
      suggested: { start_date: "2026-07-12", extends_from: "2026-08-11",
                   end_date: "2026-09-10", due_date: "2026-08-23" },
      outstanding: 500,
      open_invoices: [{ id: 9, invoice_no: "INV-2607-00009", balance: 500, due_date: "2026-07-15" }],
      payment_modes: ["Cash", "Cheque", "Online Transfer", "Credit Card", "Paytm",
                      "GooglePay", "PhonePay", "Bank Transfer"],
      referenced_modes: ["Bank Transfer", "Cheque", "Credit Card", "GooglePay",
                         "Online Transfer", "Paytm", "PhonePay"],
      gst_percent: 18, due_days: 15, today: "2026-08-08",
    };
  }

  // --- renewal queue ---
  if (pathname === "/renewals") {
    const rows = list((n) => ({
      id: n, customer_id: n, customer_name: `Customer ${n}`, username: `yn_cust${n}`,
      mobile: `98765123${n}0`, zone: "Yash Net",
      kind: n === 2 ? "change" : "renew",
      kind_label: n === 2 ? "Plan change" : "Renewal",
      current_plan: "Fibre 100 Mbps",
      requested_plan: n === 2 ? "Fibre 200 Mbps" : "Fibre 100 Mbps",
      is_upgrade: n === 2, months: n === 3 ? 3 : 1, days: n === 3 ? 90 : 30,
      amount: n === 3 ? 2400 : 800, status: "pending", note: "",
      decision_note: "", invoice_id: n, invoice_no: `INV-2608-0000${n}`,
      invoice_paid: n !== 3, invoice_balance: n === 3 ? 2400 : 0,
      payment_id: n !== 3 ? n : null, current_expiry: "2026-08-1" + n,
      effective_from: "", effective_to: "", decided_at: "", decided_by: "",
      created_at: "2026-08-0" + n,
    }));
    return { data: rows, __envelope: { totals: {
      count: 3, amount: 4000, paid: 2, unpaid: 1 } } };
  }
  if (pathname === "/renewals/counts") {
    return { pending: 3, approved: 12, rejected: 1, cancelled: 0,
             pending_paid: 2, pending_amount: 4000 };
  }
  if (pathname === "/renewals/due") {
    const rows = list((n) => ({
      customer_id: n, customer_plan_id: n, name: `Customer ${n}`,
      username: `yn_cust${n}`, mobile: n === 3 ? "" : `98765123${n}0`,
      has_mobile: n !== 3, zone: "Yash Net", plan_name: "Fibre 100 Mbps",
      end_date: "2026-08-1" + n, days_left: n + 1,
    }));
    return { data: rows, __envelope: { totals: {
      count: 3, without_mobile: 1, days: 7 } } };
  }

  // --- billing run ---
  if (pathname === "/billing/run/preview") {
    const rows = list((n) => ({
      customer_id: n, customer_plan_id: n, name: `Customer ${n}`,
      username: `yn_cust${n}`, mobile: `98765123${n}0`, zone: "Yash Net",
      area: `Sector-${n}`, building: "Shri Sai Darshan Chs",
      plan_name: "Fibre 100 Mbps", current_expiry: `2026-08-1${n}`,
      period_start: `2026-08-1${n}`, period_end: `2026-09-1${n}`,
      amount: 800, discount: n === 2 ? 80 : 0, net_amount: n === 2 ? 720 : 800,
      due_date: "2026-08-23", billable: n !== 3,
      blocked_reason: n === 3 ? "Already invoiced (INV-2608-00007, covered to 09-09-2026)." : "",
      existing_invoice_no: n === 3 ? "INV-2608-00007" : "",
    }), 4);
    return { data: rows, __envelope: { totals: {
      listed: 4, billable: 3, blocked: 1, amount: 2320,
      issue_date: "2026-08-08", due_days: 15 } } };
  }
  if (pathname === "/billing/run/filters") {
    return {
      zones: ["Yash Net"], areas: ["Sector-1", "Sector-2"],
      buildings: ["Shri Sai Darshan Chs"], localities: ["Airoli"],
      plans: [{ id: 1, name: "Fibre 100 Mbps" }, { id: 2, name: "Fibre 200 Mbps" }],
      active_plans: 4, default_due_days: 15,
    };
  }

  // --- authorising report ---
  if (pathname === "/payments/authorisation-queue") {
    const rows = list((n) => ({
      id: n, customer_id: n, name: `Customer ${n}`, username: `yn_cust${n}`,
      flat_no: `B/${200 + n}`, building: "Shri Sai Darshan Chs", area: `Sector-${n}`,
      locality: `Locality ${n}`, zone: "Yash Net",
      mode: ["Online Transfer", "Cash", "GooglePay"][n - 1] || "Cash",
      details: n === 1 ? "2451785833493, FGUPII4480FD71911" : "Cash",
      receipt_no: `R428${n}`, amount: 3000 + n * 100, discount: n === 2 ? 300 : 0,
      outstanding: n === 3 ? 500 : 0, receipt_date: "2026-08-0" + n,
      recorded_at: "2026-08-0" + n, agent: "Sumedh", invoice_id: n,
      invoice_no: `INV-2608-0000${n}`, source: "admin", status: "approved",
    }));
    return { data: rows, __envelope: { totals: {
      amount: 9600, discount: 300, count: 3 } } };
  }
  if (pathname === "/payments/authorisation-filters") {
    return {
      localities: ["Locality 1", "Locality 2", "Locality 3"],
      areas: ["Sector-1", "Sector-2", "Sector-3"],
      buildings: ["Shri Sai Darshan Chs", "Vijay Chs"],
      zones: ["Yash Net"],
      modes: ["Cash", "GooglePay", "Online Transfer"],
      staff: [{ id: 1, name: "Sumedh" }, { id: 2, name: "Dinesh" }],
      pending_count: 3,
    };
  }
  if (pathname === "/payments/authorisation-summary") {
    return list((n) => ({ date: "2026-08-0" + n, count: n, amount: n * 1500 }), 4);
  }

  if (pathname === "/billing/options") {
    return {
      payment_modes: ["Cash", "Cheque", "Online Transfer", "Credit Card", "Paytm", "GooglePay", "PhonePay", "Bank Transfer"],
      referenced_modes: ["Bank Transfer", "Cheque", "Credit Card", "GooglePay", "Online Transfer", "Paytm", "PhonePay"],
      discount_reasons: [
        { id: 1, name: "Power Supply", default_amount: 50, default_percent: 0, description: "" },
        { id: 2, name: "wire supply", default_amount: 0, default_percent: 0, description: "" },
      ],
    };
  }
  if (pathname === "/plans/picker") {
    return list((n) => ({
      id: n, name: `FR_${n}00Mbps_30Days`, plan_code: `F${n}00`, plan_type: "fibre",
      speed_mbps: n * 100, validity_days: 30, service_provider_id: 1,
      service_provider: "L2S", base_amount: 677.97 * n, total_amount: 800 * n,
    }));
  }
  if (pathname === "/masters/discount-reasons") {
    return list((n) => ({ id: n, name: ["Power Supply", "wire supply", "Goodwill"][n - 1], default_amount: 0, default_percent: 0, description: "", is_active: true }));
  }
  if (/^\/invoices\/\d+$/.test(pathname)) {
    return { ...invoice(1), items: [{ id: 1, description: "Fibre 100 - monthly", amount: 799 }], customer: customerUser, payments: list(payment, 1) };
  }

  // --- collections ---
  if (pathname === "/customers") return list((n) => ({ ...customerUser, id: n, full_name: `Customer ${n}`, reference_id: `YIS-000${n}` }));
  if (pathname === "/invoices" || pathname === "/portal/invoices") return list(invoice);
  if (pathname === "/payments" || pathname === "/portal/payments") return list(payment);
  if (pathname === "/plans" || pathname === "/portal/plans") {
    return list((n) => ({ id: n, name: `Fibre ${n}00`, plan_code: `F${n}00`, plan_type: "fibre", speed_mbps: n * 100, price_monthly: 499 * n, isp_amount: 200, validity_days: 30, service_provider_id: 1, is_active: true }));
  }
  if (pathname === "/staff") return list((n) => ({ id: n, username: `staff${n}`, full_name: `Staff Member ${n}`, email: `s${n}@yash.in`, mobile: "9800000000", role: n === 1 ? "admin" : "staff", staff_type_id: 1, monthly_salary: 25000, is_active: true }));
  if (pathname === "/companies") return list((n) => ({ id: n, name: `YASH Internet ${n}`, mobile: "9800000000", email: "office@yash.in", gstin: "27ABCDE1234F1Z5", website_url: "", address: "Pune", bank_account_details: "", logo_url: null }));
  if (pathname === "/service-providers") return list(named);
  if (pathname === "/users") return [staffUser];
  if (pathname === "/notification-templates") return list((n) => ({ id: n, code: `T${n}`, name: `Template ${n}`, title: "Hello", body: "Your bill is due.", channel: "sms", send_push: false, send_whatsapp: true, is_active: true }));
  if (pathname === "/notifications") return list((n) => ({ id: n, title: `Notice ${n}`, body: "Something happened", created_at: "2026-07-01T10:00:00", read: false }));
  if (pathname === "/portal/notifications") return list((n) => ({ id: n, title: `Notice ${n}`, body: "Your invoice is ready", created_at: "2026-07-01T10:00:00", read: false }));

  // --- HR ---
  if (pathname === "/hr/leaves") return list((n) => ({ id: n, user_id: 1, start_date: "2026-07-1" + n, end_date: "2026-07-1" + (n + 1), status: ["pending", "approved", "rejected"][n - 1] || "pending", reason: "Personal" }));
  if (pathname === "/hr/attendance") return list((n) => ({ id: n, user_id: 1, date: "2026-07-0" + n, status: "present" }));
  if (pathname === "/hr/payroll") return list((n) => ({ id: n, user_id: 1, month_year: "2026-07-01", salary: 25000, paid: n % 2 === 0 }));

  // --- reports ---
  if (pathname === "/reports/plan-expiry") {
    return list((n) => ({
      customer_plan_id: n, customer_id: n, customer_name: `Customer ${n}`,
      mobile: "980000000" + n, zone: "North", plan_name: "Fibre 100",
      price: 799, start_date: "2026-07-01", end_date: "2026-08-0" + n,
      days_left: n - 1, outstanding: n === 2 ? 799 : 0,
    }), 4);
  }
  if (pathname.startsWith("/reports/")) {
    return list((n) => ({ id: n, user_id: 1, user: "Staff Member 1", customer_id: n, customer_name: `Customer ${n}`, date: "2026-07-0" + n, from_date: "2026-07-01", to_date: "2026-07-05", status: "present", reason: "-", amount: 799, month: "Jul 2026", salary: 25000, plan: "Fibre 100", end_date: "2026-08-15", days_left: 10, mobile: "9800000000", zone: "North" }));
  }
  if (pathname === "/customers/plan-status") {
    return list((n) => ({ customer_plan_id: n, customer_id: n, customer_name: `Customer ${n}`, mobile: "9800000000", zone: "North", plan: { plan_name: "Fibre 100", start_date: "2026-06-01", end_date: "2026-09-01", status: "active", price: 799 } }));
  }

  // --- settings ---
  if (pathname === "/settings") {
    // Real shape: a flat array of setting rows, grouped client-side by `group`.
    return [
      { key: "invoice_due_days", value: "15", value_type: "int", group: "billing", label: "Invoice Due Days", is_secret: false },
      { key: "invoice_prefix", value: "INV", value_type: "str", group: "billing", label: "Invoice Prefix", is_secret: false },
      { key: "sms_sender_id", value: "YASHIS", value_type: "str", group: "sms", label: "Sms Sender Id", is_secret: false },
      { key: "wa_api_key", value: "secret", value_type: "str", group: "whatsapp", label: "Wa Api Key", is_secret: true },
      { key: "cashfree_app_id", value: "", value_type: "str", group: "payment", label: "Cashfree App Id", is_secret: false },
      { key: "mail_enabled", value: "False", value_type: "bool", group: "email", label: "Mail Enabled", is_secret: false },
      { key: "mail_host", value: "smtp.gmail.com", value_type: "str", group: "email", label: "Mail Host", is_secret: false },
      { key: "mail_port", value: "587", value_type: "int", group: "email", label: "Mail Port", is_secret: false },
      { key: "mail_password", value: "", value_type: "str", group: "email", label: "Mail Password", is_secret: true },
      { key: "mail_from", value: "billing@yash.in", value_type: "str", group: "email", label: "Mail From", is_secret: false },
    ];
  }
  if (pathname === "/settings/backups") {
    return list((n) => ({ id: n, filename: `backup-${n}.sql`, size_human: `${n * 10} KB`, status: "completed", created_at: "2026-07-0" + n, download_url: `/api/v1/settings/backups/${n}/download` }));
  }
  if (pathname === "/isp/credentials") {
    return list((n) => ({
      id: n, driver: ["log2space", "synnefo", "24online"][n - 1] || "log2space",
      label: `Node ${n}`, service_provider_id: 1, service_provider: "Record 1",
      base_url: "https://isp.example.com", username: "api",
      has_secret: n !== 2, has_api_key: n === 1, nas: "", site: "",
      verify_ssl: true, timeout_seconds: 20, is_active: true, is_sandbox: n === 3,
      health: ["ok", "error", "unknown"][n - 1] || "unknown",
      last_ok_at: n === 1 ? "2026-07-01T09:00:00" : null,
      last_error: n === 2 ? "Connection refused by upstream host" : "",
    }));
  }
  if (pathname === "/isp/sync-logs") {
    return list((n) => ({
      id: n, credential_id: 1, driver: "log2space", customer_id: n,
      action: "activate", http_status: n === 2 ? 502 : 200, success: n !== 2,
      duration_ms: 120 * n, request_summary: "{}",
      response_summary: n === 2 ? "Upstream timeout" : "OK",
      created_at: "2026-07-0" + n,
    }));
  }
  if (pathname === "/messages/log") {
    return list((n) => ({
      id: n, customer_id: n, customer_name: `Customer ${n}`, channel: "whatsapp",
      body: "Your plan expires soon. Please renew.",
      status: n === 2 ? "failed" : "sent", created_at: "2026-07-0" + n,
    }));
  }
  if (pathname === "/branding") return branding;

  // Master tables all come through the generic factory.
  if (/^\/customers\/\d+\/(enable|disable|terminate|discount|note|send-sms|assign-plan|renew-plan)$/.test(pathname)) {
    return { status: "ok", network_synced: true, note: "Prefers evening calls." };
  }
  if (/^\/customers\/\d+\/reset-password$/.test(pathname)) {
    return { temporary_password: "Tmp-8sK2xQ", delivered: { sms: true, email: true, network: true } };
  }
  if (/^\/customers\/\d+\/reset-mac$/.test(pathname)) {
    return { mac_address: "AA:BB:CC:DD:EE:FF", status: "reset" };
  }

  if (pathname.startsWith("/masters/") || pathname.startsWith("/expenses") ||
      pathname.startsWith("/inventory/") || pathname === "/staff/types") {
    return list((n) => named(n, {
      code: `C${n}`, city: "Pune", state: "MH", rate: 18, amount: 500 * n,
      description: "Sample row", mobile: "9800000000", email: `r${n}@x.com`,
      expense_date: "2026-07-01", status: "paid", category_id: 1, account_id: 1, payee_id: 1,
      vendor_id: 1, product_id: 1, quantity: 10, sku: `SKU${n}`, unit_price: 100, cost_price: 80,
      bill_no: `BILL-${n}`, bill_date: "2026-07-01", due_date: "2026-07-15", total_amount: 5000,
      tax_percent: 18, contact_person: "Contact", gstin: "27ABCDE1234F1Z5", address: "Pune",
      template_type: "sms", full_name: `Record ${n}`,
    }));
  }

  return [];
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, "http://localhost");
  const { pathname } = url;

  if (pathname.startsWith("/api/v1")) {
    const apiPath = pathname.slice("/api/v1".length) || "/";
    const raw = payloadFor(apiPath);
    const extra = (raw && raw.__envelope) || {};
    const data = raw && raw.__envelope ? raw.data : raw;
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({
      ok: true, data,
      meta: Array.isArray(data) ? meta : undefined,
      ...extra,
    }));
    return;
  }

  // Static assets out of dist, with SPA fallback to index.html.
  let rel = pathname.replace(/^\/app\/?/, "") || "index.html";
  let file = path.join(DIST, rel);
  if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) {
    file = path.join(DIST, "index.html");
  }
  res.writeHead(200, { "Content-Type": MIME[path.extname(file)] || "text/plain" });
  res.end(fs.readFileSync(file));
});

server.listen(4173, () => console.log("mock server on http://localhost:4173/app/"));
