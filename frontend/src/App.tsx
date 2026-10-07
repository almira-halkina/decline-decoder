import { useEffect, useState } from "react";
import { Dashboard } from "./pages/Dashboard";
import { Store } from "./pages/Store";

// Hash routing keeps the static build deployable anywhere without rewrite rules.
function currentPage(): "store" | "dashboard" {
  return window.location.hash.startsWith("#/dashboard") ? "dashboard" : "store";
}

export function App() {
  const [page, setPage] = useState(currentPage);
  const checkoutResult = new URLSearchParams(window.location.search).get("checkout");

  useEffect(() => {
    const onHashChange = () => setPage(currentPage());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  return (
    <>
      <nav className="topbar">
        <a className="brand" href="#/">
          <span className="logo" aria-hidden="true" />
          Decline Decoder
        </a>
        <div className="nav-links">
          <a href="#/" aria-current={page === "store" ? "page" : undefined}>Store</a>
          <a href="#/dashboard" aria-current={page === "dashboard" ? "page" : undefined}>
            Dashboard
          </a>
        </div>
        <span className="test-mode">Test mode</span>
      </nav>
      <main>{page === "store" ? <Store checkoutResult={checkoutResult} /> : <Dashboard />}</main>
    </>
  );
}
