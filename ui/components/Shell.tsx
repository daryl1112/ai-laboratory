"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

function nav(pathname: string, href: string, exact = false) {
  return exact ? pathname === href : pathname.startsWith(href);
}

export default function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [clock, setClock] = useState("--:--:--");

  useEffect(() => {
    const t = setInterval(() => {
      const d = new Date();
      const p = (n: number) => String(n).padStart(2, "0");
      setClock(`${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`);
    }, 1000);
    return () => clearInterval(t);
  }, []);

  return (
    <>
      <div className="grid-bg" />
      <div className="app">
        <nav className="rail">
          <div className="logo" title="AI Laboratory">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
              <path d="M9 3h6M10 3v6l-5 8a2 2 0 0 0 1.7 3h10.6A2 2 0 0 0 19 17l-5-8V3" />
              <path d="M7 15h10" />
            </svg>
          </div>
          <Link href="/" className={`navbtn ${nav(pathname, "/", true) ? "active" : ""}`} title="Dashboard">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
              <rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" />
              <rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" />
            </svg>
          </Link>
          <Link href="/new" className={`navbtn ${nav(pathname, "/new") ? "active" : ""}`} title="New experiment">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M12 5v14M5 12h14" /></svg>
          </Link>
          <div className="rail-sp" />
          <div className="avatar">DR</div>
        </nav>

        <div className="main">
          <div className="topbar">
            <div className="crumb">LAB / <b>MISSION CONTROL</b></div>
            <div className="spacer" />
            <div className="status-chip"><span className="dot" /> ENGINE ONLINE</div>
            <div className="crumb" style={{ letterSpacing: ".05em", color: "var(--txt)" }}>{clock}</div>
          </div>
          {children}
        </div>
      </div>
    </>
  );
}
