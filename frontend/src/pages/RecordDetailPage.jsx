import { Link, useParams } from "react-router-dom";
import { useFetch } from "../api/useFetch";
import { ErrorNote, fmtDate, inr, Loading, StatusPill } from "../components/ui";

const SKIP = new Set(["id", "customer_id", "customer_plan_id", "company"]);

export default function RecordDetailPage({ type }) {
  const { id } = useParams();
  const endpoint = type === "customer" ? `/customers/${id}` : `/invoices/${id}`;
  const { data, loading, error, refetch } = useFetch(endpoint);
  if (loading) return <Loading label={`Loading ${type}`} />;
  if (error) return <ErrorNote error={error} onRetry={refetch} />;
  const record = type === "customer" ? data?.customer : data;
  if (!record) return <ErrorNote error="not_found" />;
  return <section className="page">
    <div className="page-heading"><div><h1>{type === "customer" ? record.full_name : record.invoice_no}</h1><p>{type === "customer" ? record.mobile : `Invoice for ${record.customer_name}`}</p></div><Link className="btn" to={type === "customer" ? "/customers" : "/invoices"}>Back to list</Link></div>
    <section className="panel detail-grid">{Object.entries(record).filter(([key, value]) => !SKIP.has(key) && typeof value !== "object" && value !== null && value !== "").map(([key, value]) => <div key={key}><span>{key.replaceAll("_", " ")}</span><strong>{format(key, value)}</strong></div>)}</section>
    {type === "customer" && <History title="Plans" rows={data.plans} />}
    {type === "customer" && <History title="Recent invoices" rows={data.invoices} />}
    {type === "customer" && <History title="Recent payments" rows={data.payments} />}
  </section>;
}

function format(key, value) {
  if (key.includes("amount") || key === "balance" || key === "outstanding") return inr(value);
  if (key.includes("date") || key.endsWith("_at")) return fmtDate(value);
  if (key === "status") return <StatusPill value={value} />;
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value);
}

function History({ title, rows = [] }) {
  if (!rows?.length) return null;
  return <section className="panel history"><h2>{title}</h2><div className="table-wrap"><table className="data"><thead><tr>{Object.keys(rows[0]).slice(0, 5).map((key) => <th key={key}>{key.replaceAll("_", " ")}</th>)}</tr></thead><tbody>{rows.slice(0, 10).map((row, index) => <tr key={row.id || index}>{Object.keys(rows[0]).slice(0, 5).map((key) => <td key={key}>{typeof row[key] === "object" ? "—" : format(key, row[key])}</td>)}</tr>)}</tbody></table></div></section>;
}
