import { login } from "@/app/actions";

type LoginPageProps = { searchParams: Promise<{ error?: string }> };

export default async function LoginPage({ searchParams }: LoginPageProps) {
  const { error } = await searchParams;
  return (
    <main className="login-shell">
      <form className="login-card" action={login}>
        <p className="eyebrow">MultiHub administration</p>
        <h1>TV Sources</h1>
        <p>Enter the shared dashboard password to manage the public TV media library.</p>
        <label htmlFor="password">Password</label>
        <input id="password" name="password" type="password" autoComplete="current-password" required autoFocus />
        {error === "invalid-password" ? <p className="form-error">That password was not accepted.</p> : null}
        <button className="button" type="submit">Open dashboard</button>
      </form>
    </main>
  );
}
