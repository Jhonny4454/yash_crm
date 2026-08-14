import { useAuth } from "../../context/AuthContext";

export default function DashboardHeader({ collapsed = false }) {
  const { user, company } = useAuth();

  const hour = new Date().getHours();
  const greeting =
    hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";

  const firstName = (user?.full_name || user?.username || "").trim().split(" ")[0];

  const today = new Date().toLocaleDateString("en-IN", {
    weekday: "long", day: "numeric", month: "long", year: "numeric",
  });

  return (
    <header className="dash-head">
      {/* Left Side: Greeting */}
      <div className="dash-branding">
        {/* Text Greeting */}
        <div className="dash-greeting">
          <h1>
            {/* Without a name this rendered "Good evening," with a dangling comma. */}
            {firstName ? `${greeting}, ${firstName}` : greeting}
          </h1>
          <p className="sub">
            {company?.name || "Yash Internet Services"} · {today}
          </p>
        </div>
      </div>

      {/* Right Side: Add extra action buttons here if needed */}
      <div className="dash-actions">
        {/* <button className="btn primary sm">+ Quick Action</button> */}
      </div>
    </header>
  );
}