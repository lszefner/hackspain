import { redirect } from "next/navigation";

/**
 * La mesa (desk/mock) se sirve en la raíz por un rewrite de next.config.ts.
 * Esto solo cubre el caso en que ese rewrite no llegue a aplicarse: antes
 * llevaba a /facturas, que es la interfaz que #21 retiró.
 */
export default function Home() {
  redirect("/desk/index.html");
}
