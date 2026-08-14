import { useFetch } from "../../api/useFetch";
import { ErrorNote, Loading, inrShort } from "../ui";

/** Pure SVG - no chart library, so nothing extra to install or keep updated. */
export default function RevenueChart() {
  // ✅ Use the main dashboard JSON endpoint
  const { data, loading, error, refetch } = useFetch("/api/v1/dashboard");

  if (loading) return <Panel title="Revenue"><Loading /></Panel>;
  if (error) return <Panel title="Revenue"><ErrorNote error={error} onRetry={refetch} /></Panel>;

  const trend = data?.trend || [];
  if (!trend.length) return <Panel title="Revenue"><div className="empty">No data yet</div></Panel>;

  const W = 560, H = 220, PAD = { t: 16, r: 12, b: 34, l: 56 };
  const peak = Math.max(...trend.map((t) => t.amount), 1);
  const innerW = W - PAD.l - PAD.r;
  const innerH = H - PAD.t - PAD.b;
  const barW = Math.min(52, (innerW / trend.length) * 0.6);

  return (
    <Panel title="Revenue — last 6 months"
           right={<span className="num panel-note">Peak {inrShort(peak)}</span>}>
      <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img"
           aria-label="Monthly collection for the last six months">
        {[0, 0.5, 1].map((f) => {
          const y = PAD.t + innerH * (1 - f);
          return (
            <g key={f}>
              <line x1={PAD.l} y1={y} x2={W - PAD.r} y2={y}
                    stroke="var(--line)" strokeDasharray="3 3" />
              <text x={PAD.l - 8} y={y + 4} textAnchor="end"
                    className="chart-axis">{inrShort(peak * f)}</text>
            </g>
          );
        })}
        {trend.map((t, i) => {
          const slot = innerW / trend.length;
          const x = PAD.l + slot * i + (slot - barW) / 2;
          const h = Math.max((t.amount / peak) * innerH, 2);
          const y = PAD.t + innerH - h;
          return (
            <g key={t.month}>
              <rect x={x} y={y} width={barW} height={h} rx="3"
                    fill="var(--signal)" opacity={0.85}>
                <title>{`${t.month}: ${inrShort(t.amount)}`}</title>
              </rect>
              <text x={x + barW / 2} y={H - 12} textAnchor="middle"
                    className="chart-axis">{t.month.split(" ")[0]}</text>
            </g>
          );
        })}
      </svg>
    </Panel>
  );
}

function Panel({ title, right, children }) {
  return (
    <div className="card panel-fill">
      <div className="card-head"><h2>{title}</h2>{right}</div>
      <div className="card-body">{children}</div>
    </div>
  );
}