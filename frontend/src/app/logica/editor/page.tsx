import { borradorDesde, getNorma, normaActiva } from "@/lib/normas";
import { Editor } from "./ui";

export const metadata = { title: "Redactar norma · tito.ai" };
export const dynamic = "force-dynamic";

export default async function EditorPage({
  searchParams,
}: {
  searchParams: Promise<{ desde?: string }>;
}) {
  const sp = await searchParams;
  const desde = sp.desde && getNorma(sp.desde) ? sp.desde : undefined;
  const { yaml, base } = borradorDesde(desde);
  return <Editor inicial={yaml} base={base} activa={normaActiva()} />;
}
