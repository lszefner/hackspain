import { borradorInicial, normaActiva } from "@/lib/normas";
import { Editor } from "./ui";

export const metadata = { title: "Redactar norma · Albertito" };
export const dynamic = "force-dynamic";

export default function EditorPage() {
  return <Editor inicial={borradorInicial()} base={normaActiva()} />;
}
