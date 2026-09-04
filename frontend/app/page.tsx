import { redirect } from "next/navigation";

import { logout } from "@/app/actions";
import { SourceDashboard } from "@/components/source-dashboard";
import { isAuthenticated } from "@/lib/auth";
import { listSources } from "@/lib/github";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  if (!(await isAuthenticated())) {
    redirect("/login");
  }

  const sources = await listSources();
  return (
    <main className="dashboard-shell">
      <header className="page-header">
        <div>
          <p className="eyebrow">MultiHub public library</p>
          <h1>TV Sources</h1>
          <p className="lede">Changes are committed to <code>sources/</code> on GitHub. TVs receive them on their next source refresh.</p>
        </div>
        <form action={logout}>
          <button className="button secondary" type="submit">Sign out</button>
        </form>
      </header>
      <SourceDashboard initialSources={sources} />
    </main>
  );
}
