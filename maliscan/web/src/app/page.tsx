"use client";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { Spinner } from "@/components/ui";
import { useSession } from "@/lib/session";

export default function Home() {
  const { session, loading } = useSession();
  const router = useRouter();
  useEffect(() => {
    if (!loading) router.replace(session ? "/dashboard" : "/login");
  }, [loading, session, router]);
  return <Spinner />;
}
