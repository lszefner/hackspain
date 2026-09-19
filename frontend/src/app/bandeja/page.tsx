import { data } from "@/lib/data";
import { Bandeja } from "./ui";

export const metadata = { title: "Bandeja de escalados · Albertito" };

export default async function BandejaPage() {
  const escalados = await data.bandeja();
  return <Bandeja escalados={escalados} />;
}
