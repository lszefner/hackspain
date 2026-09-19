import { SubirFactura } from "./ui";

export const metadata = { title: "Subir factura · Albertito" };
export const dynamic = "force-dynamic";

export default function SubirPage() {
  // La subida escribe en alberto.db: sin API no hay nada que hacer, y la
  // pantalla lo dice en vez de fallar al pulsar el botón.
  return <SubirFactura hayBackend={Boolean(process.env.NEXT_PUBLIC_API_BASE)} />;
}
