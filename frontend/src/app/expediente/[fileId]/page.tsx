import { redirect } from "next/navigation";
export default async function LegacyInvoice({ params }: { params: Promise<{ fileId: string }> }) {
  const { fileId } = await params;
  redirect(`/?view=invoices&invoice=${encodeURIComponent(fileId)}`);
}
