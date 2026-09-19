import { Suspense } from "react";
import { Desk } from "@/components/desk/desk";
import "./desk.css";
import "@/components/desk/journey.css";
export default function Home() {
  return (
    <Suspense fallback={<p role="status">Loading desk…</p>}>
      <Desk />
    </Suspense>
  );
}
