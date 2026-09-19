import { redirect } from "next/navigation";

/** La portada lleva directamente al listado de facturas. El muro vive en /muro. */
export default function Home() {
  redirect("/facturas");
}
