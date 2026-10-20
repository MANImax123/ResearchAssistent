import { redirect } from "next/navigation";

// The protected layout sends signed-out visitors on to /login
export default function Home() {
  redirect("/dashboard");
}
